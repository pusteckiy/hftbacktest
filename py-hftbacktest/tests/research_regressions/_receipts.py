"""Opt-in exact fill receipt reader for the isolated amend build."""
from ctypes import c_void_p,c_uint64,POINTER
import numba
from numba import njit,uint64
from hftbacktest.binding import lib,ptr_from_val,val_from_ptr,address_as_void_pointer
from hftbacktest import event_dtype
_read=lib.hashmapbt_execution_reports
_read.restype=c_void_p
_read.argtypes=[c_void_p,c_uint64,POINTER(c_uint64)]
_clear=lib.hashmapbt_clear_execution_reports
_clear.restype=None
_clear.argtypes=[c_void_p,c_uint64]
@njit(cache=True)
def reports(h,a):
 length=uint64(0);p=ptr_from_val(length)
 ptr=_read(h.ptr,a,p)
 return numba.carray(address_as_void_pointer(ptr),val_from_ptr(p),event_dtype)
@njit(cache=True)
def clear_reports(h,a):_clear(h.ptr,a)
