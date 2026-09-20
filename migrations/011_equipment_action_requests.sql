CREATE TABLE IF NOT EXISTS equipment_action_requests (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    equipment_id INT NOT NULL,
    requested_by INT NOT NULL,
    action_type VARCHAR(40) NOT NULL,
    request_data LONGTEXT NOT NULL,
    status ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Pending',
    reviewed_by INT NULL,
    reviewed_at DATETIME NULL,
    review_details VARCHAR(500) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_equipment_action_requests_status (status, created_at),
    INDEX idx_equipment_action_requests_equipment (equipment_id, created_at),
    FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL
);
