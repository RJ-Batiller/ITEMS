CREATE TABLE IF NOT EXISTS organization_requests (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type ENUM('Category','Office') NOT NULL,
    requested_by INT NOT NULL,
    request_data LONGTEXT NOT NULL,
    status ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Pending',
    reviewed_by INT NULL,
    reviewed_at DATETIME NULL,
    review_details VARCHAR(500) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_organization_requests_status (status, created_at),
    FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
);
