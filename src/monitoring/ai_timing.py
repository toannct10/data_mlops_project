"""Request-local elapsed stage measurements; no global mutation or synthetic times."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from time import perf_counter

_active=ContextVar('ai_stage_timings',default=None)

@contextmanager
def capture():
    values={};token=_active.set(values)
    try:yield values
    finally:_active.reset(token)

def stage(name):
    def decorate(fn):
        @wraps(fn)
        def timed(*args,**kwargs):
            values=_active.get()
            if values is None:return fn(*args,**kwargs)
            start=perf_counter()
            try:return fn(*args,**kwargs)
            finally:values[name]=values.get(name,0)+perf_counter()-start
        return timed
    return decorate
