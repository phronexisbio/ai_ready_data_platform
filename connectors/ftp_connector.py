"""FTP connector — cloud storage connector (BUILD_PLAN.md §3, deferred in
Phase 7, built out here).

Pulls a fixed watchlist of remote file paths from an FTP server — same
"fixed list re-fetched each run, no incremental state" shape as the other
public-source connectors. Anonymous login by default (matching how public
FTP mirrors like NCBI's are actually accessed); a real username/password
pair can be supplied for private servers. Modality is guessed from the
remote path's extension via `local_connector.guess_modality`.
"""

import argparse
import io
import os
from ftplib import FTP
from pathlib import PurePosixPath

from connectors.base import Connector, DiscoveredItem, FetchedItem
from connectors.local_connector import guess_modality

DEFAULT_HOST = "ftp.ncbi.nlm.nih.gov"
DEFAULT_PATHS = ["/pub/taxonomy/taxdump_readme.txt", "/pub/taxonomy/Major_taxonomic_updates_2023.txt"]


class FTPConnector(Connector):
    source = "ftp"

    def __init__(self, host: str, paths: list[str], user: str = "anonymous", password: str = "anonymous", **kwargs):
        super().__init__(**kwargs)
        self.host = host
        self.paths = paths
        self.user = user
        self.password = password

    def discover(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                name=PurePosixPath(path).name,
                uri=f"ftp://{self.host}{path}",
                modality=guess_modality(PurePosixPath(path)),
                metadata={"path": path},
            )
            for path in self.paths
        ]

    def fetch(self, item: DiscoveredItem) -> FetchedItem:
        buf = io.BytesIO()
        ftp = FTP(self.host, timeout=30)
        try:
            ftp.login(user=self.user, passwd=self.password)
            ftp.retrbinary(f"RETR {item.metadata['path']}", buf.write)
        finally:
            ftp.quit()
        return FetchedItem(name=item.name, content=buf.getvalue(), modality=item.modality, metadata=item.metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="ftp-nightly-sync")
    parser.add_argument("--owner", default="ftp-connector")
    parser.add_argument("--host", default=os.environ.get("FTP_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--paths",
        default=os.environ.get("FTP_PATHS", ",".join(DEFAULT_PATHS)),
        help="comma-separated remote FTP file paths",
    )
    parser.add_argument("--user", default=os.environ.get("FTP_USER", "anonymous"))
    parser.add_argument("--password", default=os.environ.get("FTP_PASSWORD", "anonymous"))
    args = parser.parse_args()

    paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    landed = FTPConnector(host=args.host, paths=paths, user=args.user, password=args.password).run(
        dataset_id=args.dataset_id, owner=args.owner
    )
    for f in landed:
        print(f)


if __name__ == "__main__":
    main()
