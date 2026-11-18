-- Names and ids from a stack's database, for scripts/public_check.py denylist
-- (`make denylist <env>`): people, groups, apps, places, custom roles, tenant
-- and subscription ids. Output goes to .git/info/denylist-db, never to a file git tracks.
with v(x) as (
  select display_name from principals
  union select principal_name from role_assignments
  union select principal_name from role_eligibilities
  union select member_name from group_memberships
  union select person_name from responsibilities union select person_email from responsibilities
  union select person_name from responsible_suggestions union select person_email from responsible_suggestions
  union select caller from activity_events where caller like '%@%'
  union select actor_name from access_changes union select via_group_name from access_changes
  union select name from scopes
  union select scope_name from role_assignments
  union select role_name from role_definitions where role_type <> 'BuiltInRole'
  union select tenant_id from auth_settings union select client_id from auth_settings
  union select tenant_id from azure_credentials union select client_id from azure_credentials
  union select subscription_id from activity_pulls
  union select id from scopes
  union select principal_id from role_assignments
  union select principal_id from role_eligibilities
  union select member_id from group_memberships
  union select reviewer_name from review_rounds
  union select created_by_name from role_assignments
  union select scope from role_assignments
  union select scope_name from role_eligibilities union select scope from role_eligibilities
  union select principal_name from access_changes union select scope_name from access_changes
  union select scope from access_changes union select actor_id from access_changes
  union select actor_name from audit_events
  union select principal_name from review_items union select scope_name from review_items
  union select decided_by_name from review_items
  union select principal_name from todos union select scope_name from todos
  union select reviewer_name from todos
  union select scope_name from assessments union select created_by_name from assessments
  union select distinct resource_id from activity_events
  union select initiator_name from directory_audit_events union select initiator_id from directory_audit_events
  union select group_name from directory_audit_events union select group_id from directory_audit_events
  union select member_name from directory_audit_events union select member_id from directory_audit_events
  union select direct_group_name from access_changes
)
select distinct trim(x) from v where x is not null and length(trim(x)) >= 5;
