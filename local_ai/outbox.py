"""Persist dispatch before I/O. Unknown delivery is never automatically resent."""
import time
from .tool_errors import ToolFailure


class Outbox:
    def init_outbox(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS outbox(job_id TEXT,part TEXT,state TEXT,
            error TEXT,updated REAL,PRIMARY KEY(job_id,part))''')
        self.db.commit()

    def admit_delivery(self, job_id, part):
        if self.receipt(job_id,part):return False
        row=self.db.execute('SELECT state FROM outbox WHERE job_id=? AND part=?',(job_id,part)).fetchone()
        if row and row[0] in ('sending','uncertain'):
            raise ToolFailure('delivery_uncertain','消息发送可能已经生效，但没有可靠回执；暂停自动重复发送。',uncertain=True)
        with self.db:self.db.execute('INSERT OR REPLACE INTO outbox VALUES (?,?,\'sending\',NULL,?)',(job_id,part,time.time()))
        return True

    def delivery_failed(self,job_id,part,error):
        # Explicit Telegram rejection is known not to have committed. Transport
        # disconnects, cancellation, and process death cannot establish that fact.
        from telegram.error import BadRequest,Forbidden,RetryAfter
        known=isinstance(error,(BadRequest,Forbidden,RetryAfter))
        state='retryable' if isinstance(error,RetryAfter) else 'rejected' if known else 'uncertain'
        with self.db:self.db.execute('UPDATE outbox SET state=?,error=?,updated=? WHERE job_id=? AND part=?',
            (state,type(error).__name__,time.time(),job_id,part))
        return state
