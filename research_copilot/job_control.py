"""Small OS lock for an existing persistent task, released on process exit."""
import os
from .errors import CopilotError
class TaskLock:
    def __init__(self,root,task_id):
        if not task_id.isalnum():raise CopilotError('translation_job','任务标识无效。','translation')
        folder=root/'translation_task_locks';folder.mkdir(exist_ok=True)
        self.file=open(folder/(task_id+'.lock'),'a+b')
        if self.file.tell()==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise CopilotError('translation_job_running','这个任务已在另一个窗口运行，请先暂停，避免重复请求。','translation') from None
    def close(self):
        self.file.close()  # Closing the descriptor releases the OS lock.

class StopToken:
    def __init__(self,event,store,job_id,run_id):self.event,self.store,self.job_id,self.run_id=event,store,job_id,run_id
    def is_set(self):
        return self.event.is_set() or any(r.get('job_id')==self.job_id and r.get('run_id')==self.run_id for r in self.store.records('translation_job_stop'))
    def set(self):self.event.set()
