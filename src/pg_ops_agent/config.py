"""Settings come from the environment, never from the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # The one namespace and cluster this server may look at. The agent cannot
    # choose another: tools take no namespace or cluster argument.
    namespace: str
    cluster: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            namespace=os.environ.get("PG_OPS_NAMESPACE", "orders"),
            cluster=os.environ.get("PG_OPS_CLUSTER", "orders-db"),
        )
