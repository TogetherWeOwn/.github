SELECT * FROM transfers WHERE id = ?;
UPDATE transfers SET url = ? WHERE version = ? AND id = ?;
