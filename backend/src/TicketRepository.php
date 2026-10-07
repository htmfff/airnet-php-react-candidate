<?php
class TicketRepository
{
    public function all()
    {
        $rows = pg_fetch_all(query('SELECT t.id, t.title, t.status, s.name AS source_name FROM tickets t LEFT JOIN sources s ON s.id=t.source_id ORDER BY t.id')) ?: array();
        foreach ($rows as &$row) {
            $row['id'] = (int) $row['id'];
        }
        return $rows;
    }

    public function find($id)
    {
        $row = pg_fetch_assoc(query('SELECT id, title, description, status, source_id, updated_at FROM tickets WHERE id=$1', array($id)));
        if (!$row) {
            return null;
        }
        $source = null;
        if ($row['source_id'] !== null) {
            $source = pg_fetch_assoc(query('SELECT id, name FROM sources WHERE id=$1', array($row['source_id'])));
        }
        return array(
            'id' => (int) $row['id'],
            'title' => $row['title'],
            'description' => $row['description'],
            'status' => $row['status'],
            'source' => $source ? array('id' => (int) $source['id'], 'name' => $source['name']) : null,
            'updated_at' => $row['updated_at'],
        );
    }

    public function sources()
    {
        $rows = pg_fetch_all(query('SELECT id, name FROM sources ORDER BY id')) ?: array();
        foreach ($rows as &$row) {
            $row['id'] = (int) $row['id'];
        }
        return $rows;
    }

    public function history($id)
    {
        $rows = pg_fetch_all(query('SELECT h.id, h.employee_id, e.name AS employee_name, h.old_source_id, h.new_source_id, h.created_at FROM ticket_history h JOIN employees e ON e.id=h.employee_id WHERE h.ticket_id=$1 ORDER BY h.id DESC', array($id))) ?: array();
        foreach ($rows as &$row) {
            $row['id'] = (int) $row['id'];
            $row['employee_id'] = (int) $row['employee_id'];
            $row['old_source_id'] = $row['old_source_id'] === null ? null : (int) $row['old_source_id'];
            $row['new_source_id'] = (int) $row['new_source_id'];
        }
        return $rows;
    }

    public function changeableTicket($id)
    {
        $ticket = pg_fetch_assoc(query('SELECT id, status, source_id FROM tickets WHERE id=$1', array($id)));
        if (!$ticket) {
            respond(404, array('error' => 'not_found', 'message' => 'Заявка не найдена'));
        }
        if ($ticket['status'] === 'archived') {
            respond(403, array('error' => 'archived_ticket', 'message' => 'Архивная заявка доступна только для чтения'));
        }
        return $ticket;
    }

    public function changeSource($id, $sourceId, array $employee)
    {
        $ticket = $this->changeableTicket($id);
        if (!pg_fetch_assoc(query('SELECT id FROM sources WHERE id=$1', array($sourceId)))) {
            respond(400, array('error' => 'invalid_source', 'message' => 'Источник не найден'));
        }
        if ($ticket['source_id'] !== null && (int) $ticket['source_id'] === $sourceId) {
            return $this->find($id);
        }
        query('BEGIN');
        try {
            query('UPDATE tickets SET source_id=$1, updated_at=clock_timestamp() WHERE id=$2', array($sourceId, $id));
            query('INSERT INTO ticket_history(ticket_id, employee_id, old_source_id, new_source_id) VALUES($1,$2,$3,$4)', array($id, $employee['id'], $ticket['source_id'], $sourceId));
            query('COMMIT');
        } catch (Throwable $error) {
            try {
                query('ROLLBACK');
            } catch (Throwable $rollbackError) {
                error_log('ROLLBACK failed: ' . $rollbackError->getMessage());
            }
            throw $error;
        }
        return $this->find($id);
    }
}
