# Graph Report - AAAHHH  (2026-10-15)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 413 nodes · 1262 edges · 27 communities (14 shown, 13 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 167 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- test_auth.py
- test_ocr.py
- AuthService
- AuthorizationError
- auth.py
- Database
- test_submission.py
- database.py
- validate_corrected_fields
- Principal
- .require_role
- create_database_engine
- _get_review_policy
- seed_demo_accounts
- AI-Powered Land Record Management and Verification System
- create_database
- ApplicationSummary
- .list_landowner_approved_records
- .list_audit_events
- .get_application_for_review
- .list_officer_review_queue_filtered
- .list_approved_registry
- .get_landowner_document
- ApplicationData
- AuthUserData
- land-record-management

## God Nodes (most connected - your core abstractions)
1. `AuthService` - 79 edges
2. `Database` - 44 edges
3. `AuthorizationError` - 34 edges
4. `transaction()` - 34 edges
5. `DocumentStorage` - 27 edges
6. `User` - 25 edges
7. `Base` - 24 edges
8. `Application` - 23 edges
9. `create_database_engine()` - 19 edges
10. `review_context()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `test_unauthenticated_upload_is_rejected()` --uses--> `AuthenticationError`  [INFERRED]
  tests/test_document_upload.py → modules/auth.py
- `test_valid_upload_preserves_original_metadata_and_creates_draft()` --uses--> `Application`  [INFERRED]
  tests/test_document_upload.py → modules/database.py
- `test_approved_application_fields_cannot_be_edited()` --uses--> `Application`  [INFERRED]
  tests/test_ocr.py → modules/database.py
- `test_complete_upload_ocr_edit_submit_workflow()` --uses--> `Application`  [INFERRED]
  tests/test_submission.py → modules/database.py
- `test_invalid_submission_does_not_change_status_or_emit_success_events()` --uses--> `Application`  [INFERRED]
  tests/test_submission.py → modules/database.py

## Import Cycles
- None detected.

## Communities (27 total, 13 thin omitted)

### Community 0 - "test_auth.py"
Cohesion: 0.11
Nodes (37): AuthenticationError, hash_password(), Application, ApplicationVersion, AuthSession, Base, Document, LandRecord (+29 more)

### Community 1 - "test_ocr.py"
Cohesion: 0.08
Nodes (26): as_json_value(), _clean_value(), _compile_label_pattern(), extract_fields(), _extract_labeled_value(), FieldExtraction, fields_from_json(), OCRLine (+18 more)

### Community 2 - "AuthService"
Cohesion: 0.10
Nodes (22): AuthService, _configured_max_upload_bytes(), DocumentStorage, DocumentStorageError, DocumentTooLargeError, InvalidDocumentError, _safe_original_filename(), StoredDocument (+14 more)

### Community 3 - "AuthorizationError"
Cohesion: 0.10
Nodes (32): _application_row(), _approved_records(), _dashboard(), _my_applications(), _navigate_to_applications(), _navigate_to_upload(), _notifications(), _render_applications() (+24 more)

### Community 4 - "auth.py"
Cohesion: 0.14
Nodes (9): _application_submission(), ApplicationReviewDetail, ApplicationSubmission, _landowner_application_detail(), LandownerApplicationDetail, OfficerReviewDocument, OfficerReviewEvent, ReviewQueueFilter (+1 more)

### Community 5 - "Database"
Cohesion: 0.15
Nodes (4): Database, _document_data(), DocumentData, NotificationData

### Community 6 - "test_submission.py"
Cohesion: 0.32
Nodes (13): AuditEvent, Notification, _install_controlled_ocr(), _png_bytes(), submission_context(), test_application_numbers_are_unique_and_human_readable(), test_complete_upload_ocr_edit_submit_workflow(), test_duplicate_submission_is_rejected_without_duplicate_events() (+5 more)

### Community 7 - "database.py"
Cohesion: 0.21
Nodes (5): ApplicationSubmissionData, _audit_event_hash(), _canonical_utc(), OfficerReviewStartedData, utc_now()

### Community 8 - "validate_corrected_fields"
Cohesion: 0.32
Nodes (6): validate_corrected_fields(), _required_fields(), test_area_accepts_positive_explicit_units_without_conversion(), test_area_requires_a_positive_number_and_explicit_unit(), test_invalid_date_and_non_text_values_are_rejected(), test_required_fields_must_be_present()

### Community 11 - "create_database_engine"
Cohesion: 0.22
Nodes (5): main(), create_database_engine(), get_database_url(), initialize_database(), main()

### Community 12 - "_get_review_policy"
Cohesion: 0.18
Nodes (4): _get_review_policy(), OfficerApplicationDocumentDownload, OfficerDashboardMetrics, OfficerReviewStarted

### Community 13 - "seed_demo_accounts"
Cohesion: 0.24
Nodes (4): AccountConfigurationError, seed_demo_accounts(), DemoAccountRoleConflict, DemoUserData

### Community 14 - "AI-Powered Land Record Management and Verification System"
Cohesion: 0.22
Nodes (8): AI-Powered Land Record Management and Verification System, Configure development demo accounts, Initialize the database, Landowner document uploads, Requirements, Run a portal, Run tests, Setup

### Community 17 - ".list_landowner_approved_records"
Cohesion: 0.40
Nodes (3): _land_record_summary(), LandRecordSummary, LandRecordSummaryData

## Knowledge Gaps
- **1 isolated node(s):** `land-record-management`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 108 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AuthService` connect `AuthService` to `test_auth.py`, `test_ocr.py`, `AuthorizationError`, `auth.py`, `Database`, `test_submission.py`, `Principal`, `.require_role`, `_get_review_policy`, `ApplicationSummary`, `.list_landowner_approved_records`, `.list_audit_events`, `.list_approved_registry`, `.get_landowner_document`?**
  _High betweenness centrality (0.247) - this node is a cross-community bridge._
- **Are the 43 inferred relationships involving `AuthService` (e.g. with `Database` and `DocumentStorage`) actually correct?**
  _`AuthService` has 43 INFERRED edges - model-reasoned connections that need verification._
- **What connects `land-record-management` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_auth.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1092896174863388 - nodes in this community are weakly interconnected._
- **Why does `Database` connect `Database` to `test_auth.py`, `test_ocr.py`, `AuthService`, `auth.py`, `test_submission.py`, `database.py`, `seed_demo_accounts`, `create_database`, `.list_landowner_approved_records`, `.get_application_for_review`, `.list_officer_review_queue_filtered`, `ApplicationData`, `AuthUserData`?**
  _High betweenness centrality (0.126) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `Database` (e.g. with `AuthService` and `seed_demo_accounts()`) actually correct?**
  _`Database` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `test_ocr.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08013468013468013 - nodes in this community are weakly interconnected._