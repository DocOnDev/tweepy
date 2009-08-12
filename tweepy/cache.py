# Tweepy
# Copyright 2009 Joshua Roesslein
# See LICENSE

import time
import threading
import os
import hashlib
import fcntl
import pickle as pickle

from .error import TweepError
from . import memcache

"""Cache interface"""
class Cache(object):

  def __init__(self, timeout=60):
    """Init the cache
        timeout: number of seconds to keep a cached entry
    """
    self.timeout = timeout

  def store(self, key, value):
    """Add new record to cache
        key: entry key
        value: data of entry
    """
    raise NotImplementedError

  def get(self, key, timeout=None):
    """Get cached entry if exists and not expired
        key: which entry to get
        timeout: override timeout with this value [optional]
    """
    raise NotImplementedError

  def cleanup(self):
    """Delete any expired entries in cache."""
    raise NotImplementedError

  def flush(self):
    """Delete all cached entries"""
    raise NotImplementedError

"""In-memory cache"""
class MemoryCache(Cache):

  def __init__(self, timeout=60):
    Cache.__init__(self, timeout)
    self._entries = {}
    self.lock = threading.Lock()

  def __getstate__(self):
    # pickle
    return {'entries': self._entries, 'timeout': self.timeout}

  def __setstate__(self, state):
    # unpickle
    self.lock = threading.Lock()
    self._entries = state['entries']
    self.timeout = state['timeout']

  def _is_expired(self, entry, timeout):
    return timeout > 0 and (time.time() - entry[0]) >= timeout

  def store(self, key, value):
    with self.lock:
      self._entries[key] = (time.time(), value)

  def get(self, key, timeout=None):
    with self.lock:
      # check to see if we have this key
      entry = self._entries.get(key)
      if not entry:
        # no hit, return nothing
        return None

      # use provided timeout in arguments if provided
      # otherwise use the one provided during init.
      _timeout = self.timeout if timeout is None else timeout

      # make sure entry is not expired
      if self._is_expired(entry, _timeout):
        # entry expired, delete and return nothing
        del self._entries[key]
        return None

      # entry found and not expired, return it
      return entry[1]

  def cleanup(self):
    with self.lock:
      for k,v in list(self._entries.items()):
        if self._is_expired(v, self.timeout):
          del self._entries[k]

  def flush(self):
    with self.lock:
      self._entries.clear()

"""File-based cache"""
class FileCache(Cache):

  # locks used to make cache thread-safe
  cache_locks = {}

  def __init__(self, cache_dir, timeout=60):
    Cache.__init__(self, timeout)
    if os.path.exists(cache_dir) is False:
      os.mkdir(cache_dir)
    self.cache_dir = cache_dir
    if cache_dir in FileCache.cache_locks:
      self.lock = FileCache.cache_locks[cache_dir]
    else:
      self.lock = threading.Lock()
      FileCache.cache_locks[cache_dir] = self.lock

  def _get_path(self, key):
    md5 = hashlib.md5()
    md5.update(key.encode('ascii'))
    return os.path.join(self.cache_dir, md5.hexdigest())

  def _lock_file(self, path, exclusive=True):
    lock_path = path + '.lock'
    if exclusive is True:
      f_lock = open(lock_path, 'w')
      fcntl.lockf(f_lock, fcntl.LOCK_EX)
    else:
      f_lock = open(lock_path, 'r')
      fcntl.lockf(f_lock, fcntl.LOCK_SH)
    if os.path.exists(lock_path) is False:
      f_lock.close()
      return None
    return f_lock

  def _delete_file(self, path):
    os.remove(path)
    os.remove(path + '.lock')

  def store(self, key, value):
    path = self._get_path(key)
    with self.lock:
      # acquire lock and open file
      f_lock = self._lock_file(path)
      datafile = open(path, 'wb')

      # write data
      pickle.dump((time.time(), value), datafile)

      # close and unlock file
      datafile.close()
      f_lock.close()

  def get(self, key, timeout=None):
    return self._get(self._get_path(key), timeout)

  def _get(self, path, timeout):
    if os.path.exists(path) is False:
      # no record
      return None
    while self.lock:
      # acquire lock and open
      f_lock = self._lock_file(path, False)
      if f_lock is None:
        # does not exist
        return None
      datafile = open(path, 'rb')

      # read pickled object
      created_time, value = pickle.load(datafile)
      datafile.close()

      # check if value is expired
      _timeout = self.timeout if timeout is None else timeout
      if _timeout > 0 and (time.time() - created_time) >= _timeout:
        # expired! delete from cache
        value = None
        self._delete_file(path)

      # unlock and return result
      f_lock.close()
      return value

  def cleanup(self):
    for entry in os.listdir(self.cache_dir):
      if entry.endswith('.lock'): continue
      self._get(os.path.join(self.cache_dir, entry), None)

  def flush(self):
    for entry in os.listdir(self.cache_dir):
      if entry.endswith('.lock'): continue
      self._delete_file(os.path.join(self.cache_dir, entry))

"""Memcache client"""
class MemCache(Cache):

  def __init__(self, servers, timeout=60):
    Cache.__init__(self, timeout)
    self.client = memcache.Client(servers)

  def store(self, key, value):
    self.client.set(key, (time.time(), value), time=self.timeout)

  def get(self, key, timeout=None):
    obj = self.client.get(key)
    if obj is None:
      return None
    created_time, value = obj

    # check if value is expired
    _timeout = self.timeout if timeout is None else timeout
    if _timeout > 0 and (time.time() - created_time) >= _timeout:
      # expired! delete from cache
      self.client.delete(key)
      return None

    return value

  def cleanup(self):
    # not implemented for this cache
    return

  def flush(self):
    self.client.flush_all()

