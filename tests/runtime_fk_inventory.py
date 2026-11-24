"""Reviewed head-schema FK edges. Add deletion coverage before extending this inventory."""

EXPECTED_FOREIGN_KEYS = frozenset(
    """
account_dispositions|actor_id|users|id|NO ACTION
account_dispositions|destination_id|users|id|NO ACTION
account_dispositions|user_id|users|id|NO ACTION
account_link_export_moves|export_id|media_item_exports|id|SET NULL
account_link_export_moves|transition_id|account_link_transitions|id|CASCADE
account_link_transitions|actor_id|users|id|SET NULL
account_link_transitions|user_id|users|id|CASCADE
account_transfer_moves|export_id|media_item_exports|id|SET NULL
account_transfer_moves|user_id|users|id|NO ACTION
api_keys|user_id|users|id|CASCADE
audit_events|actor_id|users|id|NO ACTION
audit_events|target_user_id|users|id|NO ACTION
auth_sessions|user_id|users|id|CASCADE
community_connections|credential_ref|community_identities|credential_ref|NO ACTION
community_identities|user_id|users|id|NO ACTION
community_operations|actor_id|users|id|NO ACTION
community_operations|owner_id|users|id|NO ACTION
community_operations|scan_id|scans|id|RESTRICT
community_master_dismissals|link_id|community_master_links|id|CASCADE
community_master_filters|link_id|community_master_links|id|CASCADE
community_master_links|media_version_id|media_versions|id|NO ACTION
community_master_links|owner_id|users|id|NO ACTION
community_master_preferences|owner_id|users|id|NO ACTION
community_master_snapshots|link_id|community_master_links|id|CASCADE
community_master_updates|link_id|community_master_links|id|CASCADE
community_publications|operation_id|community_operations|operation_id|RESTRICT
community_sync_notifications|link_id|community_master_links|id|CASCADE
community_sync_notifications|owner_id|users|id|NO ACTION
community_sync_state|owner_id|users|id|NO ACTION
credits_detection_runs|scan_id|scans|id|CASCADE
filter_selection_rules|settings_id|filter_settings|id|CASCADE
jellyfin_account_sync|user_id|users|id|CASCADE
jellyfin_user_blocks|blocked_by|users|id|SET NULL
mcf_severity_defaults|settings_id|filter_settings|id|CASCADE
media_access_scope_sources|scope_id|media_access_scopes|id|CASCADE
media_access_scopes|user_id|users|id|CASCADE
media_item_export_regenerations|export_id|media_item_exports|id|CASCADE
media_item_export_scans|media_item_export_id|media_item_exports|id|CASCADE
media_item_export_scans|scan_id|scans|id|NO ACTION
media_item_exports|actor_id|users|id|NO ACTION
media_item_exports|media_item_id|media_items|id|NO ACTION
media_item_exports|original_author_id|users|id|NO ACTION
media_item_exports|policy_owner_id|users|id|NO ACTION
media_item_exports|review_id|saved_reviews|id|NO ACTION
media_item_exports|target_user_id|users|id|NO ACTION
media_title_aliases|title_id|media_titles|id|NO ACTION
media_title_sources|media_item_id|media_items|id|CASCADE
media_title_sources|title_id|media_titles|id|NO ACTION
media_version_locators|media_item_id|media_items|id|CASCADE
media_version_locators|version_id|media_versions|id|NO ACTION
media_versions|title_id|media_titles|id|NO ACTION
retired_community_import_targets|import_id|retired_community_imports|id|CASCADE
retired_community_import_targets|media_item_id|media_items|id|CASCADE
retired_community_imports|scan_id|scans|id|CASCADE
retired_community_operations|actor_id|users|id|NO ACTION
retired_community_operations|owner_id|users|id|NO ACTION
review_accepted_credits|review_id|saved_reviews|id|CASCADE
review_accepted_credits|source_finding_id,scan_id|scan_findings|id,scan_id|NO ACTION
review_filters|review_id|saved_reviews|id|CASCADE
review_filters|source_finding_id,source_scan_id|scan_findings|id,scan_id|NO ACTION
review_filters|source_scan_id|scans|id|NO ACTION
review_mcf_documents|review_id|saved_reviews|id|CASCADE
review_media_versions|review_id|saved_reviews|id|CASCADE
review_media_versions|version_id|media_versions|id|NO ACTION
review_policy_evidence|review_id|saved_reviews|id|CASCADE
review_policy_evidence|source_finding_id|scan_findings|id|NO ACTION
review_scan_repairs|review_id|saved_reviews|id|CASCADE
review_scan_memberships|review_id|saved_reviews|id|CASCADE
review_scan_memberships|scan_id|scans|id|NO ACTION
review_synchronizations|review_id|saved_reviews|id|CASCADE
saved_reviews|media_item_id|media_items|id|NO ACTION
saved_reviews|original_author_id|users|id|NO ACTION
saved_reviews|owner_id|users|id|NO ACTION
scan_findings|scan_id|scans|id|NO ACTION
scan_mcf_documents|scan_id|scans|id|CASCADE
scan_media_item_associations|media_item_id|media_items|id|NO ACTION
scan_media_item_associations|scan_id|scans|id|NO ACTION
scan_media_versions|scan_id|scans|id|CASCADE
scan_media_versions|version_id|media_versions|id|NO ACTION
scan_queue|scan_id|scans|id|NO ACTION
scan_stage_coverage|scan_id|scans|id|CASCADE
scans|initiator_id|users|id|NO ACTION
scans|media_item_id|media_items|id|NO ACTION
user_avatars|user_id|users|id|CASCADE
user_capability_overrides|user_id|users|id|CASCADE
user_filter_copy_rules|user_id|user_filter_preferences|user_id|CASCADE
user_filter_copy_severities|user_id|user_filter_preferences|user_id|CASCADE
user_filter_enforcements|user_id|user_filter_preferences|user_id|CASCADE
user_filter_personal_rules|user_id|user_filter_preferences|user_id|CASCADE
user_filter_preferences|user_id|users|id|CASCADE
user_filter_rule_overrides|user_id|user_filter_preferences|user_id|CASCADE
user_filter_severity_overrides|user_id|user_filter_preferences|user_id|CASCADE
user_profiles|user_id|users|id|CASCADE
user_provider_identities|user_id|users|id|CASCADE
""".strip().splitlines()
)
