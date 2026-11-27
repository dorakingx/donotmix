import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))


@pytest.fixture(scope="session")
def features():
    from mixmart.flags import features_from_env

    return features_from_env()
