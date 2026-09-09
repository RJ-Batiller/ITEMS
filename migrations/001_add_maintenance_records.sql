CREATE TABLE IF NOT EXISTS maintenance_records (
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
