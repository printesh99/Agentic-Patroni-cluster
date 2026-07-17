import asyncio

import pytest

from app import sources as S


@pytest.fixture
def registry(monkeypatch):
    # Older tests monkeypatch PEP-562 dynamic module attributes and pytest
    # restores them as concrete values; remove any such residue first.
    S.__dict__.pop("NS", None)
    S.__dict__.pop("CLUSTER_NAME", None)
    entries = {
        "a": S.ClusterConfig("a", "cluster-a", "ns-a", "http://prom", enabled=True),
        "b": S.ClusterConfig("b", "cluster-b", "ns-b", "http://prom", enabled=True),
        "off": S.ClusterConfig("off", "cluster-off", "ns-off", "http://prom", enabled=False),
        "broken": S.ClusterConfig("broken", "", "ns", "http://prom", enabled=True),
    }
    monkeypatch.setattr(S, "CLUSTER_REGISTRY", entries)
    yield entries
    S.__dict__.pop("NS", None)
    S.__dict__.pop("CLUSTER_NAME", None)


def test_explicit_cluster_resolution_fails_closed(registry):
    with pytest.raises(S.UnknownClusterError):
        S.resolve_cluster_or_raise("missing")
    with pytest.raises(S.DisabledClusterError):
        S.resolve_cluster_or_raise("off")
    with pytest.raises(S.IncompleteClusterConfigError):
        S.resolve_cluster_or_raise("broken")


@pytest.mark.asyncio
async def test_context_resets_after_success_and_exception(registry):
    before = S._active_cluster_id.get()
    dep = S.cluster_path_dependency("a")
    await anext(dep)
    assert S.CLUSTER_ID == "a"
    with pytest.raises(StopAsyncIteration):
        await anext(dep)
    assert S._active_cluster_id.get() == before

    dep = S.cluster_path_dependency("b")
    await anext(dep)
    with pytest.raises(RuntimeError):
        await dep.athrow(RuntimeError("route failed"))
    assert S._active_cluster_id.get() == before


@pytest.mark.asyncio
async def test_concurrent_contexts_do_not_exchange_cluster_state(registry):
    async def observe(cluster_id):
        dep = S.cluster_path_dependency(cluster_id)
        await anext(dep)
        try:
            await asyncio.sleep(0)
            return S.CLUSTER_ID, S.NS
        finally:
            await dep.aclose()

    assert await asyncio.gather(observe("a"), observe("b")) == [
        ("a", "ns-a"), ("b", "ns-b")
    ]
