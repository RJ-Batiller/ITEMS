CREATE DATABASE IF NOT EXISTS items_db
CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE items_db;

CREATE TABLE schema_migrations (
    version VARCHAR(120) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE roles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    description VARCHAR(255)
);

INSERT INTO roles (name,description) VALUES
('Super Admin','Highest-level system administrator'),
('Admin','Inventory and transaction administrator'),
('Worker','Limited inventory-related access');

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(150),
    role_id INT NOT NULL,
    profile_picture VARCHAR(255) NULL,
    feature_permissions JSON NULL,
    is_active TINYINT(1) DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES roles(id)
);

CREATE TABLE chat_groups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    created_by INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE chat_group_members (
    group_id INT NOT NULL,
    user_id INT NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, user_id),
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE chat_messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    group_id INT NOT NULL,
    sender_id INT NOT NULL,
    reply_to_id BIGINT NULL,
    message TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE RESTRICT,
    FOREIGN KEY (reply_to_id) REFERENCES chat_messages(id) ON DELETE SET NULL,
    INDEX idx_chat_messages_group_id (group_id, id)
);

CREATE TABLE chat_attachments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    message_id BIGINT NOT NULL,
    original_name VARCHAR(255) NOT NULL,
    stored_name VARCHAR(255) NOT NULL UNIQUE,
    mime_type VARCHAR(120) NOT NULL,
    size_bytes INT UNSIGNED NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE CASCADE
);

CREATE TABLE offices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(150) NOT NULL UNIQUE,
    description VARCHAR(255)
);

CREATE TABLE categories (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(255)
);

CREATE TABLE organization_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type ENUM('Category', 'Office', 'Account') NOT NULL,
    entity_id INT NULL,
    entity_name VARCHAR(150) NOT NULL,
    action ENUM('Created', 'Updated', 'Deleted') NOT NULL,
    details VARCHAR(500),
    user_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_organization_history_entity (entity_type, entity_id, created_at),
    INDEX idx_organization_history_created (created_at),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE equipment (
    id INT AUTO_INCREMENT PRIMARY KEY,
    asset_code VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(150) NOT NULL,
    description TEXT,
    category_id INT,
    office_id INT,
    serial_number VARCHAR(150),
    specifications TEXT,
    acquisition_date DATE,
    item_type ENUM('Consumable','Non-Consumable') NOT NULL DEFAULT 'Non-Consumable',
    status ENUM('Available','Assigned','Under Maintenance','Archived','Disposed')
        DEFAULT 'Available',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL,
    FOREIGN KEY (office_id) REFERENCES offices(id) ON DELETE SET NULL
);

CREATE TABLE accountability (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    person_name VARCHAR(150) NOT NULL,
    person_position VARCHAR(150),
    office_id INT,
    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    released_at DATETIME NULL,
    is_current TINYINT(1) DEFAULT 1,
    current_equipment_id INT GENERATED ALWAYS AS
        (IF(is_current = 1, equipment_id, NULL)) STORED,
    UNIQUE KEY uq_accountability_current (current_equipment_id),
    INDEX idx_accountability_equipment_current (equipment_id, is_current),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY (office_id) REFERENCES offices(id) ON DELETE SET NULL
);

CREATE TABLE transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    user_id INT NOT NULL,
    action VARCHAR(80) NOT NULL,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_transactions_equipment_created (equipment_id, created_at),
    INDEX idx_transactions_user_created (user_id, created_at),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE maintenance_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    started_by INT NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    remarks TEXT NOT NULL,
    status ENUM('Open','Completed') NOT NULL DEFAULT 'Open',
    completed_by INT,
    completed_at DATETIME NULL,
    completion_remarks TEXT,
    INDEX idx_maintenance_equipment_status (equipment_id, status),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY (started_by) REFERENCES users(id),
    FOREIGN KEY (completed_by) REFERENCES users(id)
);

CREATE TABLE archives (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    archived_by INT NOT NULL,
    reason TEXT,
    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (equipment_id) REFERENCES equipment(id),
    FOREIGN KEY (archived_by) REFERENCES users(id)
);

CREATE TABLE disposals (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    disposed_by INT NOT NULL,
    reason TEXT NOT NULL,
    disposal_date DATE NOT NULL,
    reference_no VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_disposals_equipment (equipment_id),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id),
    FOREIGN KEY (disposed_by) REFERENCES users(id)
);

CREATE TABLE qr_codes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL UNIQUE,
    code_value VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE
);

INSERT INTO schema_migrations (version) VALUES
('001_add_maintenance_records.sql'),
('002_add_profile_pictures.sql'),
('003_add_equipment_item_type.sql'),
('004_add_feature_permissions.sql'),
('005_add_messaging.sql'),
('006_add_message_edit_delete.sql'),
('007_add_message_replies.sql'),
('008_add_category_office_history.sql'),
('009_add_account_history.sql');

INSERT INTO offices(name,description) VALUES
('Institute of Computing Studies','Sample office'),
('Administration','Sample office'),
('Registrar','Sample office');

INSERT INTO categories(name,description) VALUES
('Desktop Computer','Computer units'),
('Laptop','Portable computers'),
('Printer','Printing equipment'),
('Networking Equipment','Network devices'),
('Scanner','Scanning equipment'),
('Other','Other institutional equipment');

-- Create the Super Admin securely with: py seed_superadmin.py
-- The default development username is superadmin1. Set SUPERADMIN_PASSWORD
-- in .env instead of storing a usable password in this schema file.
