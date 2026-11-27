import os
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT: Path = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# vnpy.trader.utility 导入时按当前目录决定 .vntrader 位置，vnpy.trader.setting 导入时读取 vt_setting.json。
# 必须在第一次 import vnpy 之前切到带空 .vntrader 的临时目录，避免读写用户真实配置。
ORIGINAL_CWD: str = os.getcwd()
TRADER_TEMP_DIR: tempfile.TemporaryDirectory = tempfile.TemporaryDirectory()
Path(TRADER_TEMP_DIR.name).joinpath(".vntrader").mkdir()
os.chdir(TRADER_TEMP_DIR.name)

from vnpy.trader.setting import SETTINGS  # noqa: E402

# vnpy.trader.logger 导入时按 log.file 创建日志目录，需在它被导入前关闭。
SETTINGS["log.file"] = False
SETTINGS["log.console"] = False


def pytest_unconfigure(config: pytest.Config) -> None:
    os.chdir(ORIGINAL_CWD)
    TRADER_TEMP_DIR.cleanup()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep local evidence tests runnable without publishing their source data."""
    evidence = REPO_ROOT / "docs/evidence"
    d04 = "m1-d04/shfe-20220907/operator_slice_approval.json"
    d05 = "m1-d05/cu2211-20220907/research_valuation_policy.json"
    d08 = "m1-d08/cu2211-20220907/operator_chain_approval.json"
    d09 = "m1-d09/operator_catalog_approval.json"
    cache = "m1-d12/T12-LOCAL-CHAIN-CACHE-r107/local-option-cache.sqlite3"
    archive = (d04, d05, d08, d09)
    modules = {
        "test_research_verified_slice_m1_d04.py": (d04,),
        "test_research_archived_valuation_m1_d05.py": (d04, d05),
        "test_research_api_m1_d06.py": (d04, d05),
        "test_research_chain_archive_m1_d08.py": (d04, d05, d08),
        "test_research_chain_api_m1_d08.py": (d04, d05, d08),
        "test_research_multi_chain_m1_d09.py": archive,
        "test_research_catalog_api_m1_d09.py": archive,
        "test_research_strategy_archive_m1_d12.py": archive,
        "test_research_historical_ch_actual_m1_d12.py": archive,
        "test_local_framework_pricing_parity.py": (cache,),
        "test_research_local_cache.py": (cache,),
    }
    individual = {
        ("test_research_strategy_contract_m1_d12.py", "test_actual_legacy_openapi_compatibility"): archive,
        ("test_research_historical_provider_m1_d12.py", "test_reviewed_d09_raw_rows_replay_uses_pinned_legacy_terms"): archive,
        ("test_local_preview_access.py", "test_local_access_and_ordinary_api_auth"): archive,
        ("test_research_multi_chain_m1_d09.py", "test_old_pair_pricing_parity_with_actual_d08_browser_result"):
            (*archive, "m1-d08/cu2211-20220907/actual_chain_api_result.json"),
    }
    for item in items:
        name = item.path.name
        test_name = item.name.split("[", 1)[0]
        required = individual.get((name, test_name), modules.get(name, ()))
        missing = [path for path in required if not (evidence / path).is_file()]
        if missing:
            item.add_marker(pytest.mark.skip(reason=f"Local reviewed evidence absent: {missing[0]}"))
