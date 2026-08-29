from __future__ import annotations

import sqlite3


MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "001_teacher_foundation",
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('student','teacher')),
            display_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS terms (
            term_id TEXT PRIMARY KEY,
            term_name TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            starts_on TEXT,
            ends_on TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_id, term_name)
        );
        CREATE TABLE IF NOT EXISTS classes (
            class_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            term_id TEXT NOT NULL,
            class_name TEXT NOT NULL,
            teacher_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(term_id) REFERENCES terms(term_id) ON DELETE RESTRICT,
            UNIQUE(course_id, term_id, class_name)
        );
        CREATE TABLE IF NOT EXISTS class_memberships (
            class_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            anonymous_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(class_id, student_id),
            FOREIGN KEY(class_id) REFERENCES classes(class_id) ON DELETE CASCADE,
            UNIQUE(class_id, anonymous_id)
        );
        CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id, term_id);
        CREATE INDEX IF NOT EXISTS idx_members_student ON class_memberships(student_id, status);
        """,
    ),
    (
        "002_ingestion_and_versions",
        """
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            job_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','review_required','ready','failed','cancelled')),
            progress REAL NOT NULL DEFAULT 0,
            parser_config_hash TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            total_pages INTEGER NOT NULL DEFAULT 0,
            completed_pages INTEGER NOT NULL DEFAULT 0,
            failed_pages INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS document_pages (
            page_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            parse_method TEXT NOT NULL DEFAULT '',
            confidence REAL,
            source_image_path TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            UNIQUE(document_id, page_number)
        );
        CREATE TABLE IF NOT EXISTS document_blocks (
            block_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_id TEXT,
            block_order INTEGER NOT NULL DEFAULT 0,
            block_type TEXT NOT NULL CHECK(block_type IN ('title','paragraph','formula','table','image','code','list')),
            markdown TEXT NOT NULL DEFAULT '',
            plain_text TEXT NOT NULL DEFAULT '',
            latex TEXT NOT NULL DEFAULT '',
            source_image_path TEXT NOT NULL DEFAULT '',
            page_number INTEGER,
            bbox_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL,
            source_method TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'review_required' CHECK(verification_status IN ('auto_verified','review_required','teacher_verified','rejected')),
            visibility_level TEXT NOT NULL DEFAULT 'PUBLIC' CHECK(visibility_level IN ('PUBLIC','GUIDANCE','ASSESSMENT','VAULT')),
            parser_name TEXT NOT NULL DEFAULT '',
            parser_version TEXT NOT NULL DEFAULT '',
            document_version INTEGER NOT NULL DEFAULT 1,
            search_aliases_json TEXT NOT NULL DEFAULT '[]',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            FOREIGN KEY(page_id) REFERENCES document_pages(page_id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_versions (
            version_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','superseded')),
            created_by TEXT NOT NULL,
            published_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            UNIQUE(course_id, version_number)
        );
        CREATE TABLE IF NOT EXISTS knowledge_version_blocks (
            version_id TEXT NOT NULL,
            block_id TEXT NOT NULL,
            PRIMARY KEY(version_id, block_id),
            FOREIGN KEY(version_id) REFERENCES knowledge_versions(version_id) ON DELETE CASCADE,
            FOREIGN KEY(block_id) REFERENCES document_blocks(block_id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_course ON ingestion_jobs(course_id, status);
        CREATE INDEX IF NOT EXISTS idx_doc_blocks_document ON document_blocks(document_id, page_number, block_order);
        CREATE INDEX IF NOT EXISTS idx_versions_course ON knowledge_versions(course_id, status);
        """,
    ),
    (
        "003_student_source_visibility",
        """
        ALTER TABLE course_documents ADD COLUMN student_file_visible INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_documents_student_visible
            ON course_documents(course_id, student_file_visible, status);
        """,
    ),
    (
        "004_student_import_and_content_routing",
        """
        ALTER TABLE users ADD COLUMN student_number TEXT;
        ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE users ADD COLUMN password_changed_at TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_student_number
            ON users(student_number) WHERE student_number IS NOT NULL;

        ALTER TABLE document_blocks ADD COLUMN content_destination TEXT NOT NULL DEFAULT 'unclassified'
            CHECK(content_destination IN ('knowledge','question_bank','excluded','unclassified'));
        ALTER TABLE document_blocks ADD COLUMN semantic_role TEXT NOT NULL DEFAULT '';
        ALTER TABLE document_blocks ADD COLUMN analysis_confidence REAL;
        ALTER TABLE document_blocks ADD COLUMN analysis_reason TEXT NOT NULL DEFAULT '';
        ALTER TABLE document_blocks ADD COLUMN question_group_key TEXT NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_blocks_destination
            ON document_blocks(document_id,content_destination,page_number,block_order);

        CREATE TABLE IF NOT EXISTS question_bank_items (
            item_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            question_group_key TEXT NOT NULL,
            question_type TEXT NOT NULL DEFAULT 'other',
            stem_markdown TEXT NOT NULL DEFAULT '',
            answer_markdown TEXT NOT NULL DEFAULT '',
            explanation_markdown TEXT NOT NULL DEFAULT '',
            knowledge_points_json TEXT NOT NULL DEFAULT '[]',
            source_pages_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            UNIQUE(document_id,question_group_key)
        );
        CREATE TABLE IF NOT EXISTS question_bank_attachments (
            attachment_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            block_id TEXT NOT NULL,
            attachment_type TEXT NOT NULL CHECK(attachment_type IN ('image','table')),
            source_image_path TEXT NOT NULL DEFAULT '',
            page_number INTEGER,
            bbox_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES question_bank_items(item_id) ON DELETE CASCADE,
            FOREIGN KEY(block_id) REFERENCES document_blocks(block_id) ON DELETE RESTRICT,
            UNIQUE(item_id,block_id)
        );
        CREATE TABLE IF NOT EXISTS question_bank_versions (
            version_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','superseded')),
            created_by TEXT NOT NULL,
            published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(course_id,version_number),
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS question_bank_version_items (
            version_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            PRIMARY KEY(version_id,item_id),
            FOREIGN KEY(version_id) REFERENCES question_bank_versions(version_id) ON DELETE CASCADE,
            FOREIGN KEY(item_id) REFERENCES question_bank_items(item_id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_question_bank_course
            ON question_bank_items(course_id,status,updated_at);
        """,
    ),
    (
        "005_semantic_knowledge_structure",
        """
        CREATE TABLE IF NOT EXISTS semantic_analysis_jobs (
            analysis_job_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','retry_wait','review_required','completed','failed','cancelled')),
            current_stage TEXT NOT NULL DEFAULT 'queued',
            current_batch INTEGER NOT NULL DEFAULT 0,
            total_batches INTEGER NOT NULL DEFAULT 0,
            api_calls INTEGER NOT NULL DEFAULT 0,
            token_usage INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            last_response_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_semantic_jobs_document
            ON semantic_analysis_jobs(document_id,status,created_at);

        CREATE TABLE IF NOT EXISTS knowledge_nodes (
            node_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            document_id TEXT,
            node_scope TEXT NOT NULL CHECK(node_scope IN ('document','course')),
            parent_id TEXT,
            node_type TEXT NOT NULL CHECK(node_type IN ('chapter','section','knowledge_point')),
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            source_pages_json TEXT NOT NULL DEFAULT '[]',
            sort_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected')),
            analysis_job_id TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            FOREIGN KEY(parent_id) REFERENCES knowledge_nodes(node_id) ON DELETE SET NULL,
            FOREIGN KEY(analysis_job_id) REFERENCES semantic_analysis_jobs(analysis_job_id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_outline
            ON knowledge_nodes(course_id,node_scope,document_id,parent_id,sort_order);

        CREATE TABLE IF NOT EXISTS knowledge_node_sources (
            node_id TEXT NOT NULL,
            block_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            page_number INTEGER,
            bbox_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY(node_id,block_id),
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(node_id) ON DELETE CASCADE,
            FOREIGN KEY(block_id) REFERENCES document_blocks(block_id) ON DELETE RESTRICT,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS knowledge_relations (
            relation_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            relation_type TEXT NOT NULL CHECK(relation_type IN ('parallel','prerequisite','follow_up','related','confusable')),
            confidence REAL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','approved','rejected')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(source_node_id) REFERENCES knowledge_nodes(node_id) ON DELETE CASCADE,
            FOREIGN KEY(target_node_id) REFERENCES knowledge_nodes(node_id) ON DELETE CASCADE,
            UNIQUE(course_id,source_node_id,target_node_id,relation_type)
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_relations_course
            ON knowledge_relations(course_id,status,relation_type);

        ALTER TABLE knowledge_versions ADD COLUMN markdown_snapshot TEXT NOT NULL DEFAULT '';
        CREATE TABLE IF NOT EXISTS knowledge_version_nodes (
            version_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            PRIMARY KEY(version_id,node_id),
            FOREIGN KEY(version_id) REFERENCES knowledge_versions(version_id) ON DELETE CASCADE,
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(node_id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS knowledge_version_relations (
            version_id TEXT NOT NULL,
            relation_id TEXT NOT NULL,
            PRIMARY KEY(version_id,relation_id),
            FOREIGN KEY(version_id) REFERENCES knowledge_versions(version_id) ON DELETE CASCADE,
            FOREIGN KEY(relation_id) REFERENCES knowledge_relations(relation_id) ON DELETE RESTRICT
        );
        ALTER TABLE question_bank_items ADD COLUMN knowledge_node_id TEXT REFERENCES knowledge_nodes(node_id) ON DELETE SET NULL;
        """,
    ),
    (
        "006_teacher_document_artifacts",
        """
        CREATE TABLE IF NOT EXISTS document_artifacts (
            artifact_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL CHECK(artifact_type IN ('canonical_markdown','preview_pdf')),
            stored_path TEXT NOT NULL DEFAULT '',
            content_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','failed','unavailable')),
            generator_name TEXT NOT NULL DEFAULT '',
            generator_version TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            UNIQUE(document_id,artifact_type)
        );
        CREATE INDEX IF NOT EXISTS idx_document_artifacts_document
            ON document_artifacts(document_id,artifact_type,status);

        ALTER TABLE semantic_analysis_jobs ADD COLUMN analyzer_version TEXT NOT NULL DEFAULT 'semantic-map-reduce-v1';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN prompt_version TEXT NOT NULL DEFAULT 'teacher-knowledge-v1';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        "007_teacher_knowledge_governance",
        """
        CREATE TABLE IF NOT EXISTS document_material_metadata (
            document_id TEXT PRIMARY KEY,
            material_type TEXT NOT NULL DEFAULT 'other'
                CHECK(material_type IN (
                    'syllabus','lesson_plan','slides','textbook','experiment',
                    'question_bank','knowledge_graph','teaching_schedule','other'
                )),
            suggested_material_type TEXT NOT NULL DEFAULT 'other'
                CHECK(suggested_material_type IN (
                    'syllabus','lesson_plan','slides','textbook','experiment',
                    'question_bank','knowledge_graph','teaching_schedule','other'
                )),
            classification_status TEXT NOT NULL DEFAULT 'suggested'
                CHECK(classification_status IN ('suggested','confirmed')),
            tags_json TEXT NOT NULL DEFAULT '[]',
            classification_reason TEXT NOT NULL DEFAULT '',
            classified_by TEXT,
            classified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS knowledge_node_trash (
            node_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            trash_batch_id TEXT NOT NULL,
            original_parent_id TEXT,
            reason TEXT NOT NULL DEFAULT '',
            action_type TEXT NOT NULL DEFAULT 'teacher_rejected'
                CHECK(action_type IN ('teacher_rejected','merged','split')),
            trashed_by TEXT NOT NULL,
            trashed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(node_id) ON DELETE CASCADE,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_material_metadata_type
            ON document_material_metadata(material_type,classification_status);
        CREATE INDEX IF NOT EXISTS idx_knowledge_node_trash_course
            ON knowledge_node_trash(course_id,trashed_at);
        CREATE INDEX IF NOT EXISTS idx_knowledge_node_trash_batch
            ON knowledge_node_trash(trash_batch_id);

        INSERT OR IGNORE INTO knowledge_node_trash(
            node_id,course_id,trash_batch_id,original_parent_id,reason,action_type,trashed_by,trashed_at
        )
        SELECT node_id,course_id,'legacy-trash-' || node_id,parent_id,
               '迁移前已驳回的知识点','teacher_rejected',
               COALESCE(reviewed_by,'system'),COALESCE(reviewed_at,updated_at)
        FROM knowledge_nodes WHERE status='rejected';
        """,
    ),
    (
        "008_semantic_retry_schedule",
        """
        ALTER TABLE semantic_analysis_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE semantic_analysis_jobs ADD COLUMN next_retry_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_semantic_jobs_retry
            ON semantic_analysis_jobs(status,next_retry_at,updated_at);
        """,
    ),
    (
        "009_reviewed_question_bank",
        """
        ALTER TABLE question_bank_items ADD COLUMN options_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE question_bank_items ADD COLUMN correct_answer_json TEXT NOT NULL DEFAULT '""';
        ALTER TABLE question_bank_items ADD COLUMN difficulty TEXT NOT NULL DEFAULT '';
        ALTER TABLE question_bank_items ADD COLUMN duration_seconds INTEGER;
        ALTER TABLE question_bank_items ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'document_extracted';
        ALTER TABLE question_bank_items ADD COLUMN import_row_number INTEGER;
        ALTER TABLE question_bank_items ADD COLUMN import_id TEXT;

        CREATE TABLE IF NOT EXISTS question_bank_imports (
            import_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'review_required'
                CHECK(status IN ('review_required','published','failed')),
            total_rows INTEGER NOT NULL DEFAULT 0,
            valid_rows INTEGER NOT NULL DEFAULT 0,
            invalid_rows INTEGER NOT NULL DEFAULT 0,
            errors_json TEXT NOT NULL DEFAULT '[]',
            imported_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            UNIQUE(course_id,sha256)
        );
        CREATE INDEX IF NOT EXISTS idx_question_bank_import_course
            ON question_bank_imports(course_id,created_at);

        CREATE TABLE IF NOT EXISTS question_bank_attempts (
            attempt_id TEXT PRIMARY KEY,
            submission_id TEXT NOT NULL,
            course_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            response_json TEXT NOT NULL DEFAULT '""',
            is_correct INTEGER NOT NULL CHECK(is_correct IN (0,1)),
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(version_id) REFERENCES question_bank_versions(version_id) ON DELETE RESTRICT,
            FOREIGN KEY(item_id) REFERENCES question_bank_items(item_id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_question_attempt_course_item
            ON question_bank_attempts(course_id,item_id,submitted_at);
        CREATE INDEX IF NOT EXISTS idx_question_attempt_student
            ON question_bank_attempts(course_id,student_id,submitted_at);
        """,
    ),
    (
        "010_flexible_question_bank_import",
        """
        ALTER TABLE question_bank_imports ADD COLUMN parser_mode TEXT NOT NULL DEFAULT 'local';
        ALTER TABLE question_bank_imports ADD COLUMN detected_schema_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE question_bank_imports ADD COLUMN ai_used INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE question_bank_imports ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE question_bank_items ADD COLUMN recognition_confidence REAL NOT NULL DEFAULT 1.0;
        ALTER TABLE question_bank_items ADD COLUMN recognition_method TEXT NOT NULL DEFAULT 'local';
        ALTER TABLE question_bank_items ADD COLUMN recognition_notes_json TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (
        "011_ingestion_analysis_mode",
        """
        ALTER TABLE ingestion_jobs ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'api';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN analysis_mode TEXT NOT NULL DEFAULT 'api';
        UPDATE question_bank_items
           SET answer_markdown='T',correct_answer_json='"T"'
         WHERE question_type='true_false'
           AND lower(trim(answer_markdown)) IN ('y','yes','t','true','1','对','正确','是','√');
        UPDATE question_bank_items
           SET answer_markdown='F',correct_answer_json='"F"'
         WHERE question_type='true_false'
           AND lower(trim(answer_markdown)) IN ('n','no','f','false','0','错','错误','否','×');
        """,
    ),
    (
        "012_question_folders_and_job_ai",
        """
        CREATE TABLE IF NOT EXISTS question_bank_folders (
            folder_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            folder_name TEXT NOT NULL,
            folder_type TEXT NOT NULL CHECK(folder_type IN ('exam','homework','chapter')),
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published')),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            UNIQUE(course_id,folder_name)
        );
        ALTER TABLE question_bank_items ADD COLUMN folder_id TEXT
            REFERENCES question_bank_folders(folder_id) ON DELETE SET NULL;
        ALTER TABLE question_bank_imports ADD COLUMN folder_id TEXT
            REFERENCES question_bank_folders(folder_id) ON DELETE SET NULL;
        ALTER TABLE question_bank_versions ADD COLUMN folder_id TEXT
            REFERENCES question_bank_folders(folder_id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS idx_question_items_folder
            ON question_bank_items(course_id,folder_id,status,updated_at);
        CREATE INDEX IF NOT EXISTS idx_question_versions_folder
            ON question_bank_versions(course_id,folder_id,status,published_at);

        ALTER TABLE ingestion_jobs ADD COLUMN ai_provider TEXT NOT NULL DEFAULT '';
        ALTER TABLE ingestion_jobs ADD COLUMN ai_base_url TEXT NOT NULL DEFAULT '';
        ALTER TABLE ingestion_jobs ADD COLUMN ai_model TEXT NOT NULL DEFAULT '';
        ALTER TABLE ingestion_jobs ADD COLUMN ai_key_encrypted TEXT NOT NULL DEFAULT '';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN ai_provider TEXT NOT NULL DEFAULT '';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN ai_base_url TEXT NOT NULL DEFAULT '';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN ai_model TEXT NOT NULL DEFAULT '';
        ALTER TABLE semantic_analysis_jobs ADD COLUMN ai_key_encrypted TEXT NOT NULL DEFAULT '';

        UPDATE question_bank_items
           SET answer_markdown='Y',correct_answer_json='"Y"'
         WHERE question_type='true_false' AND answer_markdown='T'
           AND replace(lower(options_json),' ','') LIKE '%"text":"y"%'
           AND replace(lower(options_json),' ','') LIKE '%"text":"n"%';
        UPDATE question_bank_items
           SET answer_markdown='N',correct_answer_json='"N"'
         WHERE question_type='true_false' AND answer_markdown='F'
           AND replace(lower(options_json),' ','') LIKE '%"text":"y"%'
           AND replace(lower(options_json),' ','') LIKE '%"text":"n"%';
        """,
    ),
    (
        "013_adaptive_pdf_ingestion",
        """
        ALTER TABLE ingestion_jobs ADD COLUMN pipeline_stage TEXT NOT NULL DEFAULT 'queued';
        ALTER TABLE ingestion_jobs ADD COLUMN manifest_path TEXT NOT NULL DEFAULT '';
        ALTER TABLE ingestion_jobs ADD COLUMN batch_size INTEGER NOT NULL DEFAULT 40;
        ALTER TABLE ingestion_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE IF NOT EXISTS document_batches (
            batch_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            batch_number INTEGER NOT NULL,
            original_page_start INTEGER NOT NULL,
            original_page_end INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING','PROCESSING','PARSED_OK','PARSED_PARTIAL','TEXT_ONLY','SUSPECT','FAILED')),
            retry_count INTEGER NOT NULL DEFAULT 0,
            completed_pages INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            artifact_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE,
            UNIQUE(document_id,batch_number)
        );
        CREATE INDEX IF NOT EXISTS idx_document_batches_document
            ON document_batches(document_id,batch_number,status);

        ALTER TABLE document_pages ADD COLUMN page_index INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN batch_number INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE document_pages ADD COLUMN page_type TEXT NOT NULL DEFAULT 'UNKNOWN';
        ALTER TABLE document_pages ADD COLUMN parse_level TEXT NOT NULL DEFAULT 'NORMAL';
        ALTER TABLE document_pages ADD COLUMN native_text_chars INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN text_chars INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN parsed_text_chars INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN block_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN equation_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN table_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN image_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN image_area_ratio REAL NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN include_as_navigation INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_pages ADD COLUMN include_as_knowledge INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE document_pages ADD COLUMN validation_issues_json TEXT NOT NULL DEFAULT '[]';
        UPDATE document_pages SET page_index=page_number-1 WHERE page_number>0;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_document_pages_global
            ON document_pages(document_id,page_index);

        ALTER TABLE document_blocks ADD COLUMN page_index INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_blocks ADD COLUMN page_type TEXT NOT NULL DEFAULT 'UNKNOWN';
        ALTER TABLE document_blocks ADD COLUMN parse_level TEXT NOT NULL DEFAULT 'NORMAL';
        ALTER TABLE document_blocks ADD COLUMN chapter_path_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE document_blocks ADD COLUMN include_as_navigation INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE document_blocks ADD COLUMN include_as_knowledge INTEGER NOT NULL DEFAULT 1;
        UPDATE document_blocks SET page_index=page_number-1 WHERE page_number>0;
        """,
    ),
    (
        "014_knowledge_boundaries_and_fast_inspection",
        """
        ALTER TABLE document_blocks ADD COLUMN region_type TEXT NOT NULL DEFAULT 'knowledge';
        ALTER TABLE document_blocks ADD COLUMN region_confidence REAL NOT NULL DEFAULT 0.45;
        ALTER TABLE document_blocks ADD COLUMN region_reason TEXT NOT NULL DEFAULT '';
        ALTER TABLE document_blocks ADD COLUMN parent_region_block_id TEXT NOT NULL DEFAULT '';
        ALTER TABLE document_blocks ADD COLUMN knowledge_candidate INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_document_blocks_region
            ON document_blocks(document_id,region_type,include_as_knowledge,block_order);

        CREATE TABLE IF NOT EXISTS document_structures (
            document_id TEXT PRIMARY KEY,
            outline_json TEXT NOT NULL DEFAULT '[]',
            toc_entries_json TEXT NOT NULL DEFAULT '[]',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'ok' CHECK(status IN ('ok','warning','failed')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS knowledge_candidates (
            candidate_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            knowledge_type TEXT NOT NULL DEFAULT 'concept',
            source_block_ids_json TEXT NOT NULL DEFAULT '[]',
            page_start INTEGER,
            page_end INTEGER,
            bbox_json TEXT NOT NULL DEFAULT '[]',
            markdown_content TEXT NOT NULL DEFAULT '',
            teacher_revision TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            region_type TEXT NOT NULL DEFAULT 'knowledge',
            review_status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(review_status IN ('PENDING','NEEDS_REVIEW','APPROVED','MODIFIED','REJECTED')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_document
            ON knowledge_candidates(document_id,review_status,created_at);
        CREATE INDEX IF NOT EXISTS idx_knowledge_candidates_course
            ON knowledge_candidates(course_id,review_status,updated_at);

        CREATE TABLE IF NOT EXISTS knowledge_candidate_blocks (
            candidate_id TEXT NOT NULL,
            block_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(candidate_id,block_id),
            FOREIGN KEY(candidate_id) REFERENCES knowledge_candidates(candidate_id) ON DELETE CASCADE,
            FOREIGN KEY(block_id) REFERENCES document_blocks(block_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS presentation_slides (
            document_id TEXT NOT NULL,
            slide_index INTEGER NOT NULL,
            slide_type TEXT NOT NULL DEFAULT 'SIMPLE_CONTENT',
            parse_level TEXT NOT NULL DEFAULT 'FAST',
            title TEXT NOT NULL DEFAULT '',
            shape_count INTEGER NOT NULL DEFAULT 0,
            text_count INTEGER NOT NULL DEFAULT 0,
            picture_count INTEGER NOT NULL DEFAULT 0,
            reading_order_json TEXT NOT NULL DEFAULT '[]',
            shapes_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(document_id,slide_index),
            FOREIGN KEY(document_id) REFERENCES course_documents(document_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_presentation_slides_document
            ON presentation_slides(document_id,slide_index);
        """,
    ),
    (
        "015_knowledge_paths_and_ppt_regions",
        """
        ALTER TABLE knowledge_candidates ADD COLUMN chapter_path_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE presentation_slides ADD COLUMN layout_kind TEXT NOT NULL DEFAULT 'single_column';
        ALTER TABLE presentation_slides ADD COLUMN regions_json TEXT NOT NULL DEFAULT '[]';
        """,
    ),
    (
        "016_teacher_ai_settings",
        """
        CREATE TABLE IF NOT EXISTS teacher_ai_settings (
            teacher_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'openai_compatible'
                CHECK(provider IN ('openai_compatible','gemini')),
            base_url TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            api_key_encrypted TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'untested'
                CHECK(verification_status IN ('untested','connected','failed')),
            verification_message TEXT NOT NULL DEFAULT '',
            verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(teacher_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        """,
    ),
    (
        "017_material_partitioned_course_outlines",
        """
        ALTER TABLE knowledge_nodes ADD COLUMN material_type TEXT NOT NULL DEFAULT 'other'
            CHECK(material_type IN (
                'syllabus','lesson_plan','slides','textbook','experiment',
                'question_bank','knowledge_graph','teaching_schedule','other'
            ));
        ALTER TABLE knowledge_nodes ADD COLUMN generation_id TEXT;
        ALTER TABLE knowledge_nodes ADD COLUMN source_fingerprint TEXT NOT NULL DEFAULT '';

        ALTER TABLE knowledge_relations ADD COLUMN material_type TEXT NOT NULL DEFAULT 'other'
            CHECK(material_type IN (
                'syllabus','lesson_plan','slides','textbook','experiment',
                'question_bank','knowledge_graph','teaching_schedule','other'
            ));
        ALTER TABLE knowledge_relations ADD COLUMN generation_id TEXT;

        CREATE TABLE IF NOT EXISTS course_outline_generations (
            generation_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            material_type TEXT NOT NULL
                CHECK(material_type IN (
                    'syllabus','lesson_plan','slides','textbook','experiment',
                    'question_bank','knowledge_graph','teaching_schedule','other'
                )),
            analysis_job_id TEXT,
            status TEXT NOT NULL DEFAULT 'building'
                CHECK(status IN ('building','current','superseded','failed')),
            fallback_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE,
            FOREIGN KEY(analysis_job_id) REFERENCES semantic_analysis_jobs(analysis_job_id) ON DELETE SET NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_course_outline_generation_current
            ON course_outline_generations(course_id,material_type) WHERE status='current';
        CREATE INDEX IF NOT EXISTS idx_course_outline_generation_course
            ON course_outline_generations(course_id,status,material_type);
        CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_partition
            ON knowledge_nodes(course_id,node_scope,material_type,generation_id,sort_order);
        CREATE INDEX IF NOT EXISTS idx_knowledge_relations_partition
            ON knowledge_relations(course_id,material_type,generation_id,status);

        UPDATE knowledge_nodes
           SET material_type=COALESCE((
               SELECT m.material_type FROM document_material_metadata m
               WHERE m.document_id=knowledge_nodes.document_id
           ),'other')
         WHERE node_scope='document';
        """,
    ),
    (
        "018_guided_qa_sessions",
        """
        CREATE TABLE IF NOT EXISTS guided_qa_sessions (
            session_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            question TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'initial',
            turn_count INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            history_json TEXT NOT NULL DEFAULT '[]',
            retrieval_scope TEXT NOT NULL DEFAULT 'all',
            material_type TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','revealed','closed')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(course_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_guided_qa_owner
            ON guided_qa_sessions(user_id,course_id,status,updated_at);
        """,
    ),
    (
        "019_teacher_ai_provider_expansion",
        """
        CREATE TABLE teacher_ai_settings_v2 (
            teacher_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'openai_compatible'
                CHECK(provider IN ('openai_compatible','gemini','ollama')),
            base_url TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            api_key_encrypted TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT 'untested'
                CHECK(verification_status IN ('untested','connected','failed')),
            verification_message TEXT NOT NULL DEFAULT '',
            verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(teacher_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        INSERT INTO teacher_ai_settings_v2(
            teacher_id,provider,base_url,model,api_key_encrypted,
            verification_status,verification_message,verified_at,created_at,updated_at
        )
        SELECT teacher_id,provider,base_url,model,api_key_encrypted,
               verification_status,verification_message,verified_at,created_at,updated_at
          FROM teacher_ai_settings;
        DROP TABLE teacher_ai_settings;
        ALTER TABLE teacher_ai_settings_v2 RENAME TO teacher_ai_settings;
        """,
    ),
)


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               migration_id TEXT PRIMARY KEY,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    applied = {row[0] for row in conn.execute("SELECT migration_id FROM schema_migrations")}
    for migration_id, sql in MIGRATIONS:
        if migration_id in applied:
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations(migration_id) VALUES(?)", (migration_id,))
