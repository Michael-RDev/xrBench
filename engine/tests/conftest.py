import errno

import pytest

from engine.tests.fixtures import report  # noqa: F401


@pytest.fixture
def make_symlink():
    def create(link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314 or error.errno in {
                errno.ENOSYS, errno.EOPNOTSUPP
            }:
                pytest.skip("This host does not permit creating symbolic links")
            raise
        return link
    return create
