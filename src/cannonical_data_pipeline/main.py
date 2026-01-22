import logging
import os
import sys
import json
import traceback
from contextlib import asynccontextmanager
from typing import Optional

# Ensure local `src` package is discoverable before importing it
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from keycloak import KeycloakOpenID, KeycloakAuthenticationError
from starlette import status
from starlette.middleware.cors import CORSMiddleware

# Import app settings after sys.path has been adjusted
from src.cannonical_data_pipeline.infra.commons import app_settings, get_project_details
from src.cannonical_data_pipeline.api.v1 import metrics, sync

import requests as http_request

@asynccontextmanager
async def lifespan(application: FastAPI):
    yield

# Single source of truth for API keys / security
api_keys = [getattr(app_settings, 'ACP_SERVICE_API_KEY', None)]
bearer_security = HTTPBearer(auto_error=False)

project_details = get_project_details(
    base_dir=os.getenv("BASE_DIR"),
    keys=["name", "version", "description", "title"],
)

APP_NAME = os.environ.get("APP_NAME", project_details.get('title'))

# Parse EXPOSE_PORT as int with safe fallback
def _get_port_from_env(default: int = 24895) -> int:
    raw = os.environ.get("EXPOSE_PORT") or os.environ.get("PORT")
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logging.warning("Invalid EXPOSE_PORT=%r, falling back to %s", raw, default)
        return default

EXPOSE_PORT = _get_port_from_env(24895)
OTLP_GRPC_ENDPOINT = os.environ.get("OTLP_GRPC_ENDPOINT", "http://localhost:4317")


def auth_header(
    request: Request,
    bearer_auth: Optional[HTTPAuthorizationCredentials] = Depends(bearer_security),
):
    acn = request.headers.get('assistant-config-name')
    if not acn:
        raise HTTPException(status_code=400, detail="assistant-config-name not found in headers")

    # If a bearer token was provided, check it first
    if bearer_auth:
        api_key = bearer_auth.credentials
        if api_key in api_keys:
            return {"auth_type": "bearer", "api_key": api_key}

        keycloak_env = None
        auth_env_header = request.headers.get('auth-env-name')
        if auth_env_header:
            keycloak_env = getattr(app_settings, f"keycloak_{auth_env_header}", None) or app_settings.get(f"keycloak_{auth_env_header}") if hasattr(app_settings, 'get') else None
        if not keycloak_env:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden")

        try:
            KeycloakOpenID(
                server_url=keycloak_env.URL,
                client_id=keycloak_env.CLIENT_ID,
                realm_name=keycloak_env.REALMS
            ).userinfo(api_key)
            return {"auth_type": "bearer", "api_key": api_key}
        except KeycloakAuthenticationError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden")

    # Check if the password is a valid Dataverse API key (from header 'targets-credentials')
    targets_credentials_raw = request.headers.get('targets-credentials')
    if not targets_credentials_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authentication credentials")

    try:
        targets_list = json.loads(targets_credentials_raw)
    except Exception:
        logging.error("Failed to parse targets-credentials header for %s", acn)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden")

    for target_cred in targets_list:
        if not target_cred:
            logging.error(f'Missing targets credentials for {acn}')
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden")
        api_key_target_url = target_cred.get('target-repo-name')
        api_key = target_cred.get('credentials', {}).get('password')
        if not api_key_target_url or not api_key:
            logging.error(f'Invalid target credential structure for {acn}: %r', target_cred)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden")
        dv_response = http_request.get(f"https://{api_key_target_url}/api/users/token",
                                       headers={"X-Dataverse-key": api_key}, timeout=10)
        if dv_response.status_code != 200:
            logging.error(f'Failed to get token for {acn}: {dv_response.status_code}')
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Forbidden")

        return {"auth_type": "API_KEY", "api_key": api_key}

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authentication credentials")


def pre_startup_routine(app: FastAPI) -> None:
    # Enable CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


build_date = os.environ.get("BUILD_DATE", "unknown")

os.environ["acp_version"] = f"{project_details['version']} (Build Date: {build_date})"
app = FastAPI(
    title=project_details['title'],
    description=project_details['description'],
    version=os.environ.get("acp_version", "unknown"),
    lifespan=lifespan
)
print(app_settings.to_dict())
LOG_FILE = app_settings.LOG_FILE
log_config = uvicorn.config.LOGGING_CONFIG
logging.basicConfig(filename=app_settings.LOG_FILE, level=app_settings.LOG_LEVEL,
                        format=app_settings.LOG_FORMAT)

if getattr(app_settings, 'otlp_enable', False) is False:
    logging.info("Logging configured without OTLP")
else:
    logging.info("OTLP enabled - endpoint=%s", OTLP_GRPC_ENDPOINT)
    # a_commons.set_otlp(app, APP_NAME, OTLP_GRPC_ENDPOINT, LOG_FILE, uvicorn.config.LOGGING_CONFIG)

pre_startup_routine(app)
app.include_router(metrics.router, prefix="/api/v1/metrics", tags=["metrics"])
app.include_router(sync.router, prefix="/api/v1/sync", tags=["sync"])

# Root endpoint: expose basic service info (title, version, build number)
@app.get("/", tags=["root"])
def root():
    return {
        "title": project_details.get("title"),
        "version": project_details.get("version"),
        "build": build_date,
    }


def main():
    try:
        from src.cannonical_data_pipeline.deduplication import check_duplicates as dup_mod
    except Exception:
        err = {'error': 'import_error', 'details': traceback.format_exc()}
        print(json.dumps(err, default=str), flush=True)
        sys.exit(2)

    try:
        l = ["deduplicated_individual_institution"]
        report = {}
        for table in l:
            report = dup_mod.generate_duplicates_report(table_name=table, only_with_duplicates=True)
            print(json.dumps(report, default=str), flush=True)
    except Exception:
        err = {'error': 'unhandled_exception', 'details': traceback.format_exc()}
        print(json.dumps(err, default=str), flush=True)
        sys.exit(3)

    print(json.dumps(report, default=str), flush=True)
    sys.exit(0 if report.get('error') is None else 1)


if __name__ == '__main__':
    print("Starting the application...")
    print("Database dialect:", app_settings.DB_DIALECT)
    print("Database URL:", app_settings.DB_URL)
    logging.info('START Automated Curation Platform')
    logging.info(f'APP_NAME: {APP_NAME}')
    logging.info(f'Database dialect: {app_settings.DB_DIALECT}')
    logging.info("Database URL: %s", app_settings.DB_URL)
    uvicorn.run(app, host="0.0.0.0", port=EXPOSE_PORT, log_config=log_config)
