CREATE TABLE IF NOT EXISTS organization_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type ENUM('Category', 'Office') NOT NULL,
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
