"""A0/A1离线入口；不依赖数据库、网络、模型或交易。"""
import argparse
import asyncio
import json
from pathlib import Path
from .wealth_portfolio import FixtureSnapshotSource, read_snapshot
from ...schemas.wealth_portfolio import PortfolioInput, PortfolioPlan, PortfolioSelection, CandidateGroup, AssetSummary, AssetSnapshot


def fixture_path(name: str) -> Path:
    root = Path(__file__).resolve().parents[4]/'tests/fixtures/wealth_portfolio_contract_v1'
    path = root/f'{name}.json'
    return path if path.exists() else root/f'{name.lower()}.json'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', default='F18', choices=['F01', 'F08', 'F10', 'F18'])
    parser.add_argument('--schema', action='store_true', help='export shared JSON schemas instead of snapshot')
    args = parser.parse_args()
    if args.schema:
        result = {m.__name__: m.model_json_schema() for m in (PortfolioInput, PortfolioPlan,
            PortfolioSelection, CandidateGroup, AssetSummary, AssetSnapshot)}
    else:
        bundle = json.loads(fixture_path(args.fixture).read_text(encoding='utf-8'))['input']['bundle']
        result = asyncio.run(read_snapshot(FixtureSnapshotSource(bundle), 'fixture-user')).model_dump(mode='json')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
