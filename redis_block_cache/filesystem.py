from typing import Callable, ClassVar
from fsspec import AbstractFileSystem, filesystem
from fsspec.implementations.cache_mapper import AbstractCacheMapper, create_cache_mapper
from redis import Redis


class RedisCachingFileSystem(AbstractFileSystem):
    """A caching filesystem that uses Redis as a backend, layered over another filesystem.

    This class implements chunk-wise local storage of remote files, for quick
    access after the initial download. The raw data is stored blockwise in a 
    Redis instance, keyed by the filename and block. The TTL of the blocks is 
    set to 1 day by default, but can be configured with the `ttl` parameter.
    """

    protocol: ClassVar[str | tuple[str, ...]] = ("rediscached")

    def __init__(
        self,
        target_protocol=None,
        redis_host='localhost',
        redis_port='6379',
        check_files=False,
        expiry_time=604800,
        target_options=None,
        fs=None,
        same_names: bool | None = None,
        compression=None,
        cache_mapper: AbstractCacheMapper | None = None,
        **kwargs,
    ):
        """

        Parameters
        ----------
        target_protocol: str (optional)
            Target filesystem protocol. Provide either this or ``fs``.
        redis_host: str
            the hostname of the redis instance to connect with. Defaults to
            localhost.
        redis_port: str
            the port of the redis instance to connect with. Defaults to 6379.
        check_files: bool
            Whether to explicitly see if the UID of the remote file matches
            the stored one before using. Warning: some file systems such as
            HTTP cannot reliably give a unique hash of the contents of some
            path, so be sure to set this option to False.
        expiry_time: int
            The time in seconds after which a redis copy is considered useless.
            Set to falsy to prevent expiry. The default is equivalent to one
            week.
        target_options: dict or None
            Passed to the instantiation of the FS, if fs is None.
        fs: filesystem instance
            The target filesystem to run against. Provide this or ``protocol``.
        same_names: bool (optional)
            By default, target URLs are hashed using a ``HashCacheMapper`` so
            that files from different backends with the same basename do not
            conflict. If this argument is ``true``, a ``BasenameCacheMapper``
            is used instead. Other cache mapper options are available by using
            the ``cache_mapper`` keyword argument. Only one of this and
            ``cache_mapper`` should be specified.
        compression: str (optional)
            To decompress on download. Can be 'infer' (guess from the URL name),
            one of the entries in ``fsspec.compression.compr``, or None for no
            decompression.
        cache_mapper: AbstractCacheMapper (optional)
            The object use to map from original filenames to cached filenames.
            Only one of this and ``same_names`` should be specified.
        """
        super().__init__(**kwargs)
        if fs is None and target_protocol is None:
            raise ValueError(
                "Please provide filesystem instance(fs) or target_protocol"
            )
        if not (fs is None) ^ (target_protocol is None):
            raise ValueError(
                "Both filesystems (fs) and target_protocol may not be both given."
            )
        
        self.redis = Redis(host=redis_host, port=redis_port, db=0)
        self.kwargs = target_options or {}
        self.expiry = expiry_time
        self.check_files = check_files
        self.compression = compression
        
        if same_names is not None and cache_mapper is not None:
            raise ValueError(
                "Cannot specify both same_names and cache_mapper in "
                "CachingFileSystem.__init__"
            )
        if cache_mapper is not None:
            self._mapper = cache_mapper
        else:
            self._mapper = create_cache_mapper(
                same_names if same_names is not None else False
            )
        
        self.target_protocol = (
            target_protocol
            if isinstance(target_protocol, str)
            else (fs.protocol if isinstance(fs.protocol, str) else fs.protocol[0])
        )
        self.fs = fs if fs is not None else filesystem(target_protocol, **self.kwargs)

        def _strip_protocol(path):
            # acts as a method, since each instance has a difference target
            return self.fs._strip_protocol(type(self)._strip_protocol(path))

        self._strip_protocol: Callable = _strip_protocol