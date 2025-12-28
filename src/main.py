import sys
import json
import traceback


def main():
    try:
        from src.cannonical_data_pipeline.deduplication import check_duplicates as dup_mod
    except Exception:
        err = {'error': 'import_error', 'details': traceback.format_exc()}
        print(json.dumps(err, default=str), flush=True)
        sys.exit(2)

    try:
        l = ["deduplicated_individual_institution", "deduplicated_institution_country", "deduplicated_institution_institution_role", "deduplicated_institution_organisation_type", "deduplicated_institutions", "deduplicated_institutions_kb", "discipline", "gorc_atribute", "gorc_element", "group_group", "group_resource", "individual", "individual_group", "individual_group_all", "individual_institution", "individual_member", "individual_resource", "institution", "institution_country", "institution_institution_role", "institution_mapping", "institution_organisation_type", "institution_role", "interest_group", "kb_cop_json", "keyword", "org_type", "pathway", "raw_json_upload", "relation", "resource", "resource_discipline", "resource_gorc_attribute", "resource_gorc_element", "resource_keyword", "resource_pathway", "resource_relation", "resource_right", "resource_workflow", "right", "subject_resource", "uri_type", "uuid_mapping", "website_copgroup", "website_institutions", "website_interestgroup", "website_member_institutions", "website_output", "website_user", "website_user_roles", "website_workinggroup", "workflow", "working_group"]
        l = ["deduplicated_individual_institution"]
        for table in l:
            report = dup_mod.generate_duplicates_report(table_name=table, only_with_duplicates=True)
            # Always print JSON report
            print(json.dumps(report, default=str), flush=True)
    except Exception:
        err = {'error': 'unhandled_exception', 'details': traceback.format_exc()}
        print(json.dumps(err, default=str), flush=True)
        sys.exit(3)

    # Always print JSON report
    print(json.dumps(report, default=str), flush=True)
    sys.exit(0 if report.get('error') is None else 1)


if __name__ == '__main__':
    main()
