--
-- PostgreSQL database dump
--

-- Dumped from database version 16.9 (Debian 16.9-1.pgdg120+1)
-- Dumped by pg_dump version 16.9 (Ubuntu 16.9-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: compare_tables_by_keys(text, text, json); Type: FUNCTION; Schema: public; Owner: apolimeno
--

CREATE FUNCTION public.compare_tables_by_keys(left_table text, right_table text, join_keys json) RETURNS TABLE(match_type text, uuid_left text, uuid_right text)
    LANGUAGE plpgsql
    AS $_$
DECLARE
    key JSON;
    join_conditions TEXT := '';
    join_clause TEXT;
    sql TEXT;
BEGIN
    -- Build the dynamic join conditions
    FOR key IN SELECT * FROM json_array_elements(join_keys)
    LOOP
        IF join_conditions <> '' THEN
            join_conditions := join_conditions || ' AND ';
        END IF;

        join_conditions := join_conditions || format(
            'trim(lower(CAST(a.%I AS TEXT))) = trim(lower(CAST(b.%I AS TEXT)))',
            key->>0, key->>1
        );
    END LOOP;

    join_clause := format('FROM %I a FULL OUTER JOIN %I b ON %s', left_table, right_table, join_conditions);

    -- Build final SQL
    sql := format($f$
        SELECT
            CASE
                WHEN a.%I IS NOT NULL AND b.%I IS NOT NULL THEN 'match'
                WHEN a.%I IS NOT NULL AND b.%I IS NULL THEN 'only_in_left'
                WHEN a.%I IS NULL AND b.%I IS NOT NULL THEN 'only_in_right'
                ELSE 'unknown'
            END AS match_type,
            a.%I::TEXT AS uuid_left,
            b.%I::TEXT AS uuid_right

        %s
    $f$,
        -- Comparison keys
        join_keys->0->>0, join_keys->0->>1,
        join_keys->0->>0, join_keys->0->>1,
        join_keys->0->>0, join_keys->0->>1,
        -- UUID output keys
        join_keys->0->>0,
        join_keys->0->>1,
        join_clause
    );

    RETURN QUERY EXECUTE sql;
END;
$_$;


ALTER FUNCTION public.compare_tables_by_keys(left_table text, right_table text, join_keys json) OWNER TO apolimeno;

--
-- Name: generate_custom_uuid(); Type: FUNCTION; Schema: public; Owner: apolimeno
--

CREATE FUNCTION public.generate_custom_uuid() RETURNS text
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN 'rda_graph:' || lpad(to_hex((random() * 4294967295)::bigint), 8, '0');
END;
$$;


ALTER FUNCTION public.generate_custom_uuid() OWNER TO apolimeno;

--
-- Name: preview_new_individual_rows(); Type: FUNCTION; Schema: public; Owner: apolimeno
--

CREATE FUNCTION public.preview_new_individual_rows() RETURNS TABLE(uuid_individual text, firstname character varying, lastname character varying, fullname character varying, privacy_ticked character varying, title character varying, identifier_type character varying, identifier character varying, country character varying, last_update timestamp with time zone, kb_uuid text)
    LANGUAGE plpgsql
    AS $_$
DECLARE
    join_keys JSON := '[["uuid_individual", "kb_uuid"]]'::json;
    key JSON;
    join_conditions TEXT := '';
    sql TEXT;
BEGIN
    FOR key IN SELECT * FROM json_array_elements(join_keys)
    LOOP
        IF join_conditions <> '' THEN
            join_conditions := join_conditions || ' AND ';
        END IF;
        join_conditions := join_conditions || format(
            'trim(lower(CAST(a.%I AS TEXT))) = trim(lower(CAST(b.%I AS TEXT)))',
            key->>0, key->>1
        );
    END LOOP;

    sql := format($f$
        SELECT
            generate_custom_uuid() AS uuid_individual,
            b.first_name::VARCHAR,
            b.last_name::VARCHAR,
            b.full_name::VARCHAR,
            b.privacy_ticked::VARCHAR,
            b.title::VARCHAR,
            b.identifier_type::VARCHAR,
            b.identifier::VARCHAR,
            b.country::VARCHAR,
            NOW() AS last_update,
            b.kb_uuid
        FROM website_user b
        LEFT JOIN individual a ON %s
        WHERE a.uuid_individual IS NULL
    $f$, join_conditions);

    RETURN QUERY EXECUTE sql;
END;
$_$;


ALTER FUNCTION public.preview_new_individual_rows() OWNER TO apolimeno;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: deduplicated_individual_institution; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_individual_institution (
    institution text,
    original_institution character varying,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    uuid_institution character varying,
    internal_identifier character varying,
    uuid_rda_member character varying,
    member character varying,
    uuid_country character varying,
    uuid_deprecated_institution character varying,
    id integer NOT NULL
);


ALTER TABLE public.deduplicated_individual_institution OWNER TO apolimeno;

--
-- Name: deduplicated_individual_institution_; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_individual_institution_ (
    institution text,
    original_institution character varying,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    uuid_institution character varying,
    internal_identifier character varying,
    uuid_rda_member character varying,
    member character varying
);


ALTER TABLE public.deduplicated_individual_institution_ OWNER TO apolimeno;

--
-- Name: deduplicated_individual_institution_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.deduplicated_individual_institution_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.deduplicated_individual_institution_id_seq OWNER TO apolimeno;

--
-- Name: deduplicated_individual_institution_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.deduplicated_individual_institution_id_seq OWNED BY public.deduplicated_individual_institution.id;


--
-- Name: deduplicated_institution_country; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_institution_country (
    institution text,
    original_institution character varying,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    uuid_institution character varying,
    uuid_country character varying,
    country character varying,
    uuid_deprecated_institution character varying,
    id integer NOT NULL
);


ALTER TABLE public.deduplicated_institution_country OWNER TO apolimeno;

--
-- Name: deduplicated_institution_country_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.deduplicated_institution_country_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.deduplicated_institution_country_id_seq OWNER TO apolimeno;

--
-- Name: deduplicated_institution_country_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.deduplicated_institution_country_id_seq OWNED BY public.deduplicated_institution_country.id;


--
-- Name: deduplicated_institution_institution_role; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_institution_institution_role (
    institution text,
    original_institution character varying,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    "UUID_Institution" character varying,
    "InstitutionRoleID" character varying,
    "InstitutionalRole" character varying,
    uuid_country character varying,
    uuid_deprecated_institution character varying,
    id integer NOT NULL
);


ALTER TABLE public.deduplicated_institution_institution_role OWNER TO apolimeno;

--
-- Name: deduplicated_institution_institution_role_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.deduplicated_institution_institution_role_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.deduplicated_institution_institution_role_id_seq OWNER TO apolimeno;

--
-- Name: deduplicated_institution_institution_role_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.deduplicated_institution_institution_role_id_seq OWNED BY public.deduplicated_institution_institution_role.id;


--
-- Name: deduplicated_institution_organisation_type; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_institution_organisation_type (
    institution text,
    original_institution character varying,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    uuid_institution character varying,
    uuid_org_type character varying,
    organisation_type character varying,
    uuid_country character varying,
    uuid_deprecated_institution character varying,
    id integer NOT NULL
);


ALTER TABLE public.deduplicated_institution_organisation_type OWNER TO apolimeno;

--
-- Name: deduplicated_institution_organisation_type_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.deduplicated_institution_organisation_type_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.deduplicated_institution_organisation_type_id_seq OWNER TO apolimeno;

--
-- Name: deduplicated_institution_organisation_type_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.deduplicated_institution_organisation_type_id_seq OWNED BY public.deduplicated_institution_organisation_type.id;


--
-- Name: deduplicated_institutions; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_institutions (
    institution text,
    original_institution text,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    id integer,
    institution_ror_link text,
    identifier_type text,
    user_uuid integer,
    ror_id character varying,
    ror_score character varying,
    ror_fuzzy_fallback_used character varying,
    ror_match character varying
);


ALTER TABLE public.deduplicated_institutions OWNER TO apolimeno;

--
-- Name: deduplicated_institutions_kb; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.deduplicated_institutions_kb (
    institution text,
    original_institution character varying,
    was_deduplicated boolean,
    deduplication_timestamp timestamp with time zone,
    uuid_institution character varying,
    english_name character varying,
    parent_institution character varying,
    uuid_country character varying,
    uuid_deprecated character varying,
    id integer NOT NULL,
    ror_id character varying,
    ror_fuzzy_fallback_used character varying,
    ror_score character varying,
    ror_match character varying
);


ALTER TABLE public.deduplicated_institutions_kb OWNER TO apolimeno;

--
-- Name: deduplicated_institutions_kb_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.deduplicated_institutions_kb_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.deduplicated_institutions_kb_id_seq OWNER TO apolimeno;

--
-- Name: deduplicated_institutions_kb_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.deduplicated_institutions_kb_id_seq OWNED BY public.deduplicated_institutions_kb.id;


--
-- Name: discipline; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.discipline (
    internal_identifier character varying NOT NULL,
    uuid character varying NOT NULL,
    list_item character varying NOT NULL,
    description character varying NOT NULL,
    description_source character varying NOT NULL,
    taxonomy_parent character varying NOT NULL,
    taxonomy_terms character varying NOT NULL,
    uuid_parent character varying NOT NULL,
    url character varying NOT NULL
);


ALTER TABLE public.discipline OWNER TO rda;

--
-- Name: gorc_atribute; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.gorc_atribute (
    uuid_attribute character varying NOT NULL,
    attribute character varying NOT NULL,
    description character varying NOT NULL
);


ALTER TABLE public.gorc_atribute OWNER TO rda;

--
-- Name: gorc_element; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.gorc_element (
    uuid_element character varying NOT NULL,
    element character varying NOT NULL,
    description character varying NOT NULL
);


ALTER TABLE public.gorc_element OWNER TO rda;

--
-- Name: group_group; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.group_group (
    "UUID_Group1" character varying NOT NULL,
    "Title_Group1" character varying NOT NULL,
    "Relation" character varying NOT NULL,
    "UUID_Group2" character varying NOT NULL,
    "Title_Group2" character varying NOT NULL,
    "Relation_Description" character varying NOT NULL,
    "Description_source" character varying NOT NULL,
    "Description_URL" character varying NOT NULL,
    "Description_URL_Backup" character varying NOT NULL
);


ALTER TABLE public.group_group OWNER TO rda;

--
-- Name: group_resource; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.group_resource (
    uuid_group character varying NOT NULL,
    title_group character varying NOT NULL,
    relation_uuid character varying NOT NULL,
    relation character varying NOT NULL,
    uuid_resource character varying NOT NULL,
    title_resource character varying NOT NULL
);


ALTER TABLE public.group_resource OWNER TO rda;

--
-- Name: individual; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.individual (
    uuid_individual character varying NOT NULL,
    combined_name character varying NOT NULL,
    "lastName" character varying NOT NULL,
    "firstName" character varying NOT NULL,
    "fullName" character varying NOT NULL,
    revision_id character varying NOT NULL,
    title character varying NOT NULL,
    privacy_ticked character varying NOT NULL,
    short_bio character varying NOT NULL,
    rda_page character varying NOT NULL,
    linked_in character varying NOT NULL,
    twitter character varying NOT NULL,
    identifier_type character varying NOT NULL,
    identifier character varying NOT NULL,
    source character varying NOT NULL,
    uuid_rda_country character varying NOT NULL,
    country character varying NOT NULL,
    "check" character varying NOT NULL,
    last_update character varying
);


ALTER TABLE public.individual OWNER TO rda;

--
-- Name: individual_group; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.individual_group (
    uuid_individual character varying NOT NULL,
    individual character varying NOT NULL,
    member_type character varying NOT NULL,
    uuid_group character varying NOT NULL,
    group_type character varying NOT NULL,
    group_title character varying NOT NULL
);


ALTER TABLE public.individual_group OWNER TO rda;

--
-- Name: individual_group_all; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.individual_group_all (
    uuid_group character varying NOT NULL,
    "group" character varying NOT NULL,
    "memberNode" character varying NOT NULL,
    "memberNodeBackup" character varying NOT NULL,
    uuid_individual character varying NOT NULL,
    individual character varying NOT NULL
);


ALTER TABLE public.individual_group_all OWNER TO rda;

--
-- Name: individual_institution; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.individual_institution (
    internal_identifier character varying NOT NULL,
    uuid_institution character varying NOT NULL,
    organisation character varying NOT NULL,
    uuid_rda_member character varying NOT NULL,
    member character varying NOT NULL
);


ALTER TABLE public.individual_institution OWNER TO rda;

--
-- Name: individual_member; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.individual_member (
    uuid_individual character varying NOT NULL,
    combined_name character varying NOT NULL,
    relation_uuid character varying NOT NULL,
    relation character varying NOT NULL,
    uuid_institution character varying NOT NULL,
    institution character varying NOT NULL
);


ALTER TABLE public.individual_member OWNER TO rda;

--
-- Name: individual_resource; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.individual_resource (
    uuid_individual character varying NOT NULL,
    individual character varying NOT NULL,
    relation_uuid character varying NOT NULL,
    relation character varying NOT NULL,
    uuid_resource character varying NOT NULL,
    resource character varying NOT NULL
);


ALTER TABLE public.individual_resource OWNER TO rda;

--
-- Name: institution; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.institution (
    uuid_institution character varying NOT NULL,
    institution character varying NOT NULL,
    english_name character varying NOT NULL,
    parent_institution character varying NOT NULL
);


ALTER TABLE public.institution OWNER TO rda;

--
-- Name: institution_country; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.institution_country (
    uuid_institution character varying NOT NULL,
    institution character varying NOT NULL,
    uuid_country character varying NOT NULL,
    country character varying NOT NULL
);


ALTER TABLE public.institution_country OWNER TO rda;

--
-- Name: institution_institution_role; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.institution_institution_role (
    "UUID_Institution" character varying NOT NULL,
    "Institution" character varying NOT NULL,
    "InstitutionRoleID" character varying NOT NULL,
    "InstitutionalRole" character varying NOT NULL
);


ALTER TABLE public.institution_institution_role OWNER TO rda;

--
-- Name: institution_mapping; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.institution_mapping (
    original text,
    normalized text
);


ALTER TABLE public.institution_mapping OWNER TO apolimeno;

--
-- Name: institution_organisation_type; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.institution_organisation_type (
    uuid_institution character varying NOT NULL,
    institution character varying NOT NULL,
    uuid_org_type character varying NOT NULL,
    organisation_type character varying NOT NULL
);


ALTER TABLE public.institution_organisation_type OWNER TO rda;

--
-- Name: institution_role; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.institution_role (
    internal_identifier character varying NOT NULL,
    "InstitutionRoleID" character varying NOT NULL,
    "InstitutionRole" character varying NOT NULL,
    "RDA_taxonomy" character varying NOT NULL,
    "Description" character varying NOT NULL
);


ALTER TABLE public.institution_role OWNER TO rda;

--
-- Name: interest_group; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.interest_group (
    "uuid_interestGroup" character varying NOT NULL,
    title character varying NOT NULL,
    description character varying NOT NULL,
    uuid_domain character varying NOT NULL,
    domains character varying NOT NULL,
    url character varying NOT NULL,
    status character varying NOT NULL,
    sub_status character varying NOT NULL,
    last_update character varying
);


ALTER TABLE public.interest_group OWNER TO rda;

--
-- Name: kb_cop_json; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.kb_cop_json (
    id integer NOT NULL,
    uuid_othergroup text,
    title text,
    description text,
    url text,
    domains text,
    eventtype text,
    last_update character varying
);


ALTER TABLE public.kb_cop_json OWNER TO apolimeno;

--
-- Name: kb_cop_json_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.kb_cop_json_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.kb_cop_json_id_seq OWNER TO apolimeno;

--
-- Name: kb_cop_json_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.kb_cop_json_id_seq OWNED BY public.kb_cop_json.id;


--
-- Name: keyword; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.keyword (
    uuid_keyword character varying NOT NULL,
    keyword character varying NOT NULL
);


ALTER TABLE public.keyword OWNER TO rda;

--
-- Name: org_type; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.org_type (
    internal_identifier character varying NOT NULL,
    rda_uuid character varying NOT NULL,
    organisation_type_id character varying NOT NULL,
    organisation_type character varying NOT NULL,
    link_text character varying NOT NULL,
    description character varying NOT NULL
);


ALTER TABLE public.org_type OWNER TO rda;

--
-- Name: pathway; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.pathway (
    uuid_pathway character varying NOT NULL,
    pathway character varying NOT NULL,
    description character varying NOT NULL,
    data_source character varying NOT NULL
);


ALTER TABLE public.pathway OWNER TO rda;

--
-- Name: raw_json_upload; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.raw_json_upload (
    data jsonb
);


ALTER TABLE public.raw_json_upload OWNER TO apolimeno;

--
-- Name: relation; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.relation (
    uuid_relation character varying NOT NULL,
    relation character varying NOT NULL,
    uuid_relation_type character varying NOT NULL,
    relation_type character varying NOT NULL,
    short_description character varying NOT NULL,
    description character varying NOT NULL
);


ALTER TABLE public.relation OWNER TO rda;

--
-- Name: resource; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource (
    uuid character varying NOT NULL,
    uuid_link character varying,
    uuid_rda character varying NOT NULL,
    title character varying NOT NULL,
    "alternateTitle" character varying,
    uri character varying NOT NULL,
    "backupUri" character varying,
    uri2 character varying,
    "backupUri2" character varying,
    pid_lod_type character varying,
    pid_lod character varying,
    dc_date character varying NOT NULL,
    dc_description character varying NOT NULL,
    dc_language character varying NOT NULL,
    type character varying NOT NULL,
    dc_type character varying NOT NULL,
    card_url character varying,
    source character varying,
    fragment character varying,
    uuid_uri_type character varying,
    notes character varying,
    last_update character varying,
    pathway character varying,
    pathway_uuid character varying,
    group_name character varying,
    group_uuid character varying,
    changed character varying
);


ALTER TABLE public.resource OWNER TO rda;

--
-- Name: resource_discipline; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_discipline (
    uuid_resource character varying NOT NULL,
    resource character varying NOT NULL,
    uuid_disciplines character varying NOT NULL,
    disciplines character varying NOT NULL
);


ALTER TABLE public.resource_discipline OWNER TO rda;

--
-- Name: resource_gorc_attribute; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_gorc_attribute (
    uuid_resource character varying NOT NULL,
    resource character varying NOT NULL,
    "uuid_Attribute" character varying NOT NULL,
    attribute character varying NOT NULL
);


ALTER TABLE public.resource_gorc_attribute OWNER TO rda;

--
-- Name: resource_gorc_element; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_gorc_element (
    uuid_resource character varying NOT NULL,
    resource character varying NOT NULL,
    uuid_element character varying NOT NULL,
    element character varying NOT NULL
);


ALTER TABLE public.resource_gorc_element OWNER TO rda;

--
-- Name: resource_keyword; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_keyword (
    uuid_resource character varying NOT NULL,
    uuid_keyword character varying NOT NULL
);


ALTER TABLE public.resource_keyword OWNER TO rda;

--
-- Name: resource_pathway; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_pathway (
    uuid_resource character varying NOT NULL,
    resource character varying NOT NULL,
    relation_uuid character varying NOT NULL,
    relation character varying NOT NULL,
    uuid_pathway character varying NOT NULL,
    pathway character varying NOT NULL
);


ALTER TABLE public.resource_pathway OWNER TO rda;

--
-- Name: resource_relation; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_relation (
    uuid_resource character varying NOT NULL,
    uuid_relation character varying NOT NULL,
    relation character varying NOT NULL,
    lod_pid character varying NOT NULL,
    uuid_relation_type character varying NOT NULL,
    relation_type character varying NOT NULL
);


ALTER TABLE public.resource_relation OWNER TO rda;

--
-- Name: resource_right; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_right (
    uuid_resource character varying NOT NULL,
    uuid_relation character varying NOT NULL,
    relation character varying NOT NULL,
    lod_pid character varying NOT NULL,
    type character varying NOT NULL
);


ALTER TABLE public.resource_right OWNER TO rda;

--
-- Name: resource_workflow; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.resource_workflow (
    uuid_resource character varying NOT NULL,
    title character varying NOT NULL,
    uuid_adoption_state character varying NOT NULL,
    status character varying NOT NULL
);


ALTER TABLE public.resource_workflow OWNER TO rda;

--
-- Name: right; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public."right" (
    lod_pid character varying NOT NULL,
    description character varying NOT NULL,
    type character varying NOT NULL,
    data_source character varying NOT NULL
);


ALTER TABLE public."right" OWNER TO rda;

--
-- Name: subject_resource; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.subject_resource (
    uuid_resource character varying NOT NULL,
    resource character varying NOT NULL,
    keyword character varying NOT NULL
);


ALTER TABLE public.subject_resource OWNER TO rda;

--
-- Name: uri_type; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.uri_type (
    uuid_uri_type character varying NOT NULL,
    uri_type character varying NOT NULL,
    description character varying NOT NULL
);


ALTER TABLE public.uri_type OWNER TO rda;

--
-- Name: website_copgroup; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_copgroup (
    id integer NOT NULL,
    group_uuid integer,
    kb_uuid text,
    title text,
    description text,
    primary_domain text,
    url text,
    status text,
    sub_status text,
    updated_at text
);


ALTER TABLE public.website_copgroup OWNER TO apolimeno;

--
-- Name: website_copgroup_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_copgroup_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_copgroup_id_seq OWNER TO apolimeno;

--
-- Name: website_copgroup_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_copgroup_id_seq OWNED BY public.website_copgroup.id;


--
-- Name: website_institutions; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_institutions (
    id integer NOT NULL,
    institution text,
    institution_ror_link text,
    identifier_type text,
    user_uuid integer
);


ALTER TABLE public.website_institutions OWNER TO apolimeno;

--
-- Name: website_institutions_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_institutions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_institutions_id_seq OWNER TO apolimeno;

--
-- Name: website_institutions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_institutions_id_seq OWNED BY public.website_institutions.id;


--
-- Name: website_interestgroup; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_interestgroup (
    id integer NOT NULL,
    group_uuid integer,
    kb_uuid text,
    title text,
    description text,
    primary_domain text,
    url text,
    status text,
    sub_status text,
    updated_at text
);


ALTER TABLE public.website_interestgroup OWNER TO apolimeno;

--
-- Name: website_interestgroup_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_interestgroup_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_interestgroup_id_seq OWNER TO apolimeno;

--
-- Name: website_interestgroup_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_interestgroup_id_seq OWNED BY public.website_interestgroup.id;


--
-- Name: website_member_institutions; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_member_institutions (
    id integer NOT NULL,
    institute_uuid integer,
    kb_uuid text,
    institution_title text,
    english_name text,
    country text,
    organisation_type text,
    institutional_role text,
    updated_at text
);


ALTER TABLE public.website_member_institutions OWNER TO apolimeno;

--
-- Name: website_member_institutions_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_member_institutions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_member_institutions_id_seq OWNER TO apolimeno;

--
-- Name: website_member_institutions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_member_institutions_id_seq OWNED BY public.website_member_institutions.id;


--
-- Name: website_output; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_output (
    id integer NOT NULL,
    output_uuid character varying,
    kb_uuid character varying,
    title character varying,
    rda_url character varying,
    doi_uuid character varying,
    dc_description character varying,
    dc_language character varying,
    type character varying,
    ig_title character varying,
    wg_title character varying,
    summary_file_url character varying,
    updated_at character varying,
    output_type character varying,
    output_status character varying,
    review_period_start character varying,
    review_period_end character varying,
    rda_authors jsonb,
    non_rda_authors jsonb,
    adopters jsonb,
    rda_pathways jsonb,
    group_technology_focus jsonb,
    standards jsonb,
    stakeholders jsonb,
    regions character varying,
    primary_domain character varying,
    primary_field_of_expertise character varying,
    impact_statement character varying,
    explanation character varying,
    changed character varying
);


ALTER TABLE public.website_output OWNER TO apolimeno;

--
-- Name: website_output_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_output_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_output_id_seq OWNER TO apolimeno;

--
-- Name: website_output_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_output_id_seq OWNED BY public.website_output.id;


--
-- Name: website_user; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_user (
    id integer NOT NULL,
    user_uuid integer,
    kb_uuid text,
    user_login text,
    user_email text,
    display_name text,
    first_name text,
    last_name text,
    full_name text,
    profile_modified text,
    privacy_ticked integer,
    title text,
    identifier_type text,
    identifier text,
    country text,
    member_node text
);


ALTER TABLE public.website_user OWNER TO apolimeno;

--
-- Name: website_user_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_user_id_seq OWNER TO apolimeno;

--
-- Name: website_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_user_id_seq OWNED BY public.website_user.id;


--
-- Name: website_user_roles; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_user_roles (
    id integer NOT NULL,
    user_uuid integer,
    group_uuid integer,
    is_member boolean,
    cochair boolean,
    coordinator boolean,
    secretariat_liason boolean,
    tab_liason boolean
);


ALTER TABLE public.website_user_roles OWNER TO apolimeno;

--
-- Name: website_user_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_user_roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_user_roles_id_seq OWNER TO apolimeno;

--
-- Name: website_user_roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_user_roles_id_seq OWNED BY public.website_user_roles.id;


--
-- Name: website_workinggroup; Type: TABLE; Schema: public; Owner: apolimeno
--

CREATE TABLE public.website_workinggroup (
    id integer NOT NULL,
    group_uuid integer,
    kb_uuid text,
    title text,
    description text,
    primary_domain text,
    url text,
    status text,
    sub_status text,
    updated_at text
);


ALTER TABLE public.website_workinggroup OWNER TO apolimeno;

--
-- Name: website_workinggroup_id_seq; Type: SEQUENCE; Schema: public; Owner: apolimeno
--

CREATE SEQUENCE public.website_workinggroup_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_workinggroup_id_seq OWNER TO apolimeno;

--
-- Name: website_workinggroup_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: apolimeno
--

ALTER SEQUENCE public.website_workinggroup_id_seq OWNED BY public.website_workinggroup.id;


--
-- Name: workflow; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.workflow (
    "UUID_Workflow" character varying NOT NULL,
    "WorkflowState" character varying NOT NULL,
    "Description" character varying NOT NULL
);


ALTER TABLE public.workflow OWNER TO rda;

--
-- Name: working_group; Type: TABLE; Schema: public; Owner: rda
--

CREATE TABLE public.working_group (
    uuid_working_group character varying NOT NULL,
    title character varying NOT NULL,
    description character varying NOT NULL,
    uuid_domain character varying NOT NULL,
    domains character varying NOT NULL,
    url character varying NOT NULL,
    backup_url character varying NOT NULL,
    status character varying NOT NULL,
    sub_status character varying NOT NULL,
    last_update character varying
);


ALTER TABLE public.working_group OWNER TO rda;

--
-- Name: deduplicated_individual_institution id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_individual_institution ALTER COLUMN id SET DEFAULT nextval('public.deduplicated_individual_institution_id_seq'::regclass);


--
-- Name: deduplicated_institution_country id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institution_country ALTER COLUMN id SET DEFAULT nextval('public.deduplicated_institution_country_id_seq'::regclass);


--
-- Name: deduplicated_institution_institution_role id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institution_institution_role ALTER COLUMN id SET DEFAULT nextval('public.deduplicated_institution_institution_role_id_seq'::regclass);


--
-- Name: deduplicated_institution_organisation_type id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institution_organisation_type ALTER COLUMN id SET DEFAULT nextval('public.deduplicated_institution_organisation_type_id_seq'::regclass);


--
-- Name: deduplicated_institutions_kb id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institutions_kb ALTER COLUMN id SET DEFAULT nextval('public.deduplicated_institutions_kb_id_seq'::regclass);


--
-- Name: kb_cop_json id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.kb_cop_json ALTER COLUMN id SET DEFAULT nextval('public.kb_cop_json_id_seq'::regclass);


--
-- Name: website_copgroup id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_copgroup ALTER COLUMN id SET DEFAULT nextval('public.website_copgroup_id_seq'::regclass);


--
-- Name: website_institutions id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_institutions ALTER COLUMN id SET DEFAULT nextval('public.website_institutions_id_seq'::regclass);


--
-- Name: website_interestgroup id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_interestgroup ALTER COLUMN id SET DEFAULT nextval('public.website_interestgroup_id_seq'::regclass);


--
-- Name: website_member_institutions id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_member_institutions ALTER COLUMN id SET DEFAULT nextval('public.website_member_institutions_id_seq'::regclass);


--
-- Name: website_output id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_output ALTER COLUMN id SET DEFAULT nextval('public.website_output_id_seq'::regclass);


--
-- Name: website_user id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_user ALTER COLUMN id SET DEFAULT nextval('public.website_user_id_seq'::regclass);


--
-- Name: website_user_roles id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_user_roles ALTER COLUMN id SET DEFAULT nextval('public.website_user_roles_id_seq'::regclass);


--
-- Name: website_workinggroup id; Type: DEFAULT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_workinggroup ALTER COLUMN id SET DEFAULT nextval('public.website_workinggroup_id_seq'::regclass);


--
-- Name: individual_resource PK_02ba05d24dbb106baeaaead1072; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.individual_resource
    ADD CONSTRAINT "PK_02ba05d24dbb106baeaaead1072" PRIMARY KEY (uuid_individual, uuid_resource);


--
-- Name: resource_discipline PK_0415b3647d1b7ccd5c13384db87; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_discipline
    ADD CONSTRAINT "PK_0415b3647d1b7ccd5c13384db87" PRIMARY KEY (uuid_resource, uuid_disciplines);


--
-- Name: institution_institution_role PK_14f1b7c12cd4c2098b5de7455c7; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.institution_institution_role
    ADD CONSTRAINT "PK_14f1b7c12cd4c2098b5de7455c7" PRIMARY KEY ("UUID_Institution", "InstitutionRoleID");


--
-- Name: keyword PK_17365233c2b75b771f6d2759921; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.keyword
    ADD CONSTRAINT "PK_17365233c2b75b771f6d2759921" PRIMARY KEY (uuid_keyword);


--
-- Name: workflow PK_1b1bcc9701adc21da10164d0b98; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.workflow
    ADD CONSTRAINT "PK_1b1bcc9701adc21da10164d0b98" PRIMARY KEY ("UUID_Workflow");


--
-- Name: individual_member PK_2690bae2f9e5f1d42f06855f2b5; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.individual_member
    ADD CONSTRAINT "PK_2690bae2f9e5f1d42f06855f2b5" PRIMARY KEY (uuid_individual, uuid_institution);


--
-- Name: uri_type PK_29aebf08e7f82f095fc29453bb8; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.uri_type
    ADD CONSTRAINT "PK_29aebf08e7f82f095fc29453bb8" PRIMARY KEY (uuid_uri_type);


--
-- Name: group_resource PK_3139606a48d974cf7a0bb4f7955; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.group_resource
    ADD CONSTRAINT "PK_3139606a48d974cf7a0bb4f7955" PRIMARY KEY (uuid_group, uuid_resource);


--
-- Name: resource PK_32de75c6d1b4c184a29b03a132a; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource
    ADD CONSTRAINT "PK_32de75c6d1b4c184a29b03a132a" PRIMARY KEY (uuid_rda);


--
-- Name: subject_resource PK_32e406d54e91d173c8247ae0e6a; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.subject_resource
    ADD CONSTRAINT "PK_32e406d54e91d173c8247ae0e6a" PRIMARY KEY (uuid_resource, keyword);


--
-- Name: interest_group PK_4bb969a22cfff1c3e8e5ee373a2; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.interest_group
    ADD CONSTRAINT "PK_4bb969a22cfff1c3e8e5ee373a2" PRIMARY KEY ("uuid_interestGroup");


--
-- Name: individual_group PK_52bfea1c5eaff7749140053c287; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.individual_group
    ADD CONSTRAINT "PK_52bfea1c5eaff7749140053c287" PRIMARY KEY (uuid_individual, uuid_group);


--
-- Name: individual_institution PK_58f23154c25d822f1448faaba16; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.individual_institution
    ADD CONSTRAINT "PK_58f23154c25d822f1448faaba16" PRIMARY KEY (uuid_institution, uuid_rda_member);


--
-- Name: institution_role PK_64b65cf44ec5a7e74fd897f175f; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.institution_role
    ADD CONSTRAINT "PK_64b65cf44ec5a7e74fd897f175f" PRIMARY KEY ("InstitutionRoleID");


--
-- Name: group_group PK_64e796d038dc561822e0e415f87; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.group_group
    ADD CONSTRAINT "PK_64e796d038dc561822e0e415f87" PRIMARY KEY ("UUID_Group1", "UUID_Group2");


--
-- Name: institution PK_750dde998e683a74be40579641e; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.institution
    ADD CONSTRAINT "PK_750dde998e683a74be40579641e" PRIMARY KEY (uuid_institution);


--
-- Name: pathway PK_7e1d5281d60d9a9ef310a23a549; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.pathway
    ADD CONSTRAINT "PK_7e1d5281d60d9a9ef310a23a549" PRIMARY KEY (uuid_pathway);


--
-- Name: institution_organisation_type PK_83da8acdfadad1c18ba0767898b; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.institution_organisation_type
    ADD CONSTRAINT "PK_83da8acdfadad1c18ba0767898b" PRIMARY KEY (uuid_institution, uuid_org_type);


--
-- Name: resource_relation PK_8d0f94f203f79d2ebeca7fd17cd; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_relation
    ADD CONSTRAINT "PK_8d0f94f203f79d2ebeca7fd17cd" PRIMARY KEY (uuid_resource);


--
-- Name: discipline PK_9082e79588917b9a3b62822434a; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.discipline
    ADD CONSTRAINT "PK_9082e79588917b9a3b62822434a" PRIMARY KEY (uuid);


--
-- Name: resource_gorc_element PK_90f50d58bc959e44bc0a8ecdb88; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_gorc_element
    ADD CONSTRAINT "PK_90f50d58bc959e44bc0a8ecdb88" PRIMARY KEY (uuid_resource, uuid_element);


--
-- Name: resource_gorc_attribute PK_9afc86ba115183ef4218cce3c72; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_gorc_attribute
    ADD CONSTRAINT "PK_9afc86ba115183ef4218cce3c72" PRIMARY KEY (uuid_resource, "uuid_Attribute");


--
-- Name: individual_group_all PK_a38684d4d41ab2249bc2094e776; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.individual_group_all
    ADD CONSTRAINT "PK_a38684d4d41ab2249bc2094e776" PRIMARY KEY (uuid_group, uuid_individual);


--
-- Name: resource_workflow PK_afcab39562aa1d14c94a7ccf310; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_workflow
    ADD CONSTRAINT "PK_afcab39562aa1d14c94a7ccf310" PRIMARY KEY (uuid_resource, uuid_adoption_state);


--
-- Name: resource_right PK_b003c486c922a733fb31eaec9a4; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_right
    ADD CONSTRAINT "PK_b003c486c922a733fb31eaec9a4" PRIMARY KEY (uuid_resource, lod_pid);


--
-- Name: working_group PK_b2a8b5caeb49c98b448ce4ecde6; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.working_group
    ADD CONSTRAINT "PK_b2a8b5caeb49c98b448ce4ecde6" PRIMARY KEY (uuid_working_group);


--
-- Name: gorc_atribute PK_c0e0cfdd7304919eca2ca14e4f3; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.gorc_atribute
    ADD CONSTRAINT "PK_c0e0cfdd7304919eca2ca14e4f3" PRIMARY KEY (uuid_attribute);


--
-- Name: right PK_c111d160c30c593fc5776b5a677; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public."right"
    ADD CONSTRAINT "PK_c111d160c30c593fc5776b5a677" PRIMARY KEY (lod_pid);


--
-- Name: individual PK_cab3a7498c34c63d5f234845b34; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.individual
    ADD CONSTRAINT "PK_cab3a7498c34c63d5f234845b34" PRIMARY KEY (uuid_individual);


--
-- Name: resource_pathway PK_ce0a0c22ac81db24d8369116ed3; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_pathway
    ADD CONSTRAINT "PK_ce0a0c22ac81db24d8369116ed3" PRIMARY KEY (uuid_resource, uuid_pathway);


--
-- Name: gorc_element PK_cf3db71a6d52f81282dd8b94fd1; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.gorc_element
    ADD CONSTRAINT "PK_cf3db71a6d52f81282dd8b94fd1" PRIMARY KEY (uuid_element);


--
-- Name: resource_keyword PK_d8e215413101a68ba73c3d3327e; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.resource_keyword
    ADD CONSTRAINT "PK_d8e215413101a68ba73c3d3327e" PRIMARY KEY (uuid_resource, uuid_keyword);


--
-- Name: org_type PK_e128ef5eafcd9a1569a5b52d070; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.org_type
    ADD CONSTRAINT "PK_e128ef5eafcd9a1569a5b52d070" PRIMARY KEY (rda_uuid);


--
-- Name: institution_country PK_e3d36943401d638f3ab08ef5b6b; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.institution_country
    ADD CONSTRAINT "PK_e3d36943401d638f3ab08ef5b6b" PRIMARY KEY (uuid_institution);


--
-- Name: relation PK_e8f37689730cb2b83c146c98a19; Type: CONSTRAINT; Schema: public; Owner: rda
--

ALTER TABLE ONLY public.relation
    ADD CONSTRAINT "PK_e8f37689730cb2b83c146c98a19" PRIMARY KEY (uuid_relation_type);


--
-- Name: deduplicated_individual_institution deduplicated_individual_institution_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_individual_institution
    ADD CONSTRAINT deduplicated_individual_institution_pkey PRIMARY KEY (id);


--
-- Name: deduplicated_institution_country deduplicated_institution_country_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institution_country
    ADD CONSTRAINT deduplicated_institution_country_pkey PRIMARY KEY (id);


--
-- Name: deduplicated_institution_institution_role deduplicated_institution_institution_role_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institution_institution_role
    ADD CONSTRAINT deduplicated_institution_institution_role_pkey PRIMARY KEY (id);


--
-- Name: deduplicated_institution_organisation_type deduplicated_institution_organisation_type_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institution_organisation_type
    ADD CONSTRAINT deduplicated_institution_organisation_type_pkey PRIMARY KEY (id);


--
-- Name: deduplicated_institutions_kb deduplicated_institutions_kb_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.deduplicated_institutions_kb
    ADD CONSTRAINT deduplicated_institutions_kb_pkey PRIMARY KEY (id);


--
-- Name: kb_cop_json kb_cop_json_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.kb_cop_json
    ADD CONSTRAINT kb_cop_json_pkey PRIMARY KEY (id);


--
-- Name: kb_cop_json kb_cop_json_uuid_othergroup_key; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.kb_cop_json
    ADD CONSTRAINT kb_cop_json_uuid_othergroup_key UNIQUE (uuid_othergroup);


--
-- Name: website_copgroup website_copgroup_group_uuid_key; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_copgroup
    ADD CONSTRAINT website_copgroup_group_uuid_key UNIQUE (group_uuid);


--
-- Name: website_copgroup website_copgroup_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_copgroup
    ADD CONSTRAINT website_copgroup_pkey PRIMARY KEY (id);


--
-- Name: website_institutions website_institutions_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_institutions
    ADD CONSTRAINT website_institutions_pkey PRIMARY KEY (id);


--
-- Name: website_interestgroup website_interestgroup_group_uuid_key; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_interestgroup
    ADD CONSTRAINT website_interestgroup_group_uuid_key UNIQUE (group_uuid);


--
-- Name: website_interestgroup website_interestgroup_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_interestgroup
    ADD CONSTRAINT website_interestgroup_pkey PRIMARY KEY (id);


--
-- Name: website_member_institutions website_member_institutions_institute_uuid_key; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_member_institutions
    ADD CONSTRAINT website_member_institutions_institute_uuid_key UNIQUE (institute_uuid);


--
-- Name: website_member_institutions website_member_institutions_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_member_institutions
    ADD CONSTRAINT website_member_institutions_pkey PRIMARY KEY (id);


--
-- Name: website_output website_output_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_output
    ADD CONSTRAINT website_output_pkey PRIMARY KEY (id);


--
-- Name: website_user website_user_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_user
    ADD CONSTRAINT website_user_pkey PRIMARY KEY (id);


--
-- Name: website_user_roles website_user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_user_roles
    ADD CONSTRAINT website_user_roles_pkey PRIMARY KEY (id);


--
-- Name: website_workinggroup website_workinggroup_group_uuid_key; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_workinggroup
    ADD CONSTRAINT website_workinggroup_group_uuid_key UNIQUE (group_uuid);


--
-- Name: website_workinggroup website_workinggroup_pkey; Type: CONSTRAINT; Schema: public; Owner: apolimeno
--

ALTER TABLE ONLY public.website_workinggroup
    ADD CONSTRAINT website_workinggroup_pkey PRIMARY KEY (id);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

GRANT ALL ON SCHEMA public TO rda;


--
-- PostgreSQL database dump complete
--

