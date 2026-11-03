"""Evaluate the shipped dashboard queries with Prometheus's real query engine."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "install/helm/kgateway-dashboards/dashboards"
VARIABLES = {
    name: ".*"
    for name in (
        "gateway", "namespace", "controller", "translator", "cluster", "response_code"
    )
}
STATE_GAUGES = (
    (
        6, "kgateway_resources_managed",
        {"namespace": "ns-a", "parent": "gw-a", "resource": "HTTPRoute"},
        {"resource": "HTTPRoute"},
    ),
    (
        31, "kgateway_xds_snapshot_resources",
        {"namespace": "ns-a", "gateway": "gw-a", "resource": "Listener"},
        {"gateway": "gw-a", "resource": "Listener"},
    ),
    (
        10, "kgateway_routing_domains",
        {"namespace": "ns-a", "gateway": "gw-a", "port": "80"},
        {"gateway": "gw-a", "port": "80"},
    ),
)


def label_set(labels):
    return "{" + ",".join(
        f"{key}={json.dumps(value)}" for key, value in sorted(labels.items())
    ) + "}"


def replicas(metric, values, **labels):
    return [
        {
            "series": metric + label_set({
                **labels, "pod": f"controller-{index}",
                "instance": f"10.0.0.{index + 1}:9091",
            }),
            "values": str(value),
        }
        for index, value in enumerate(values)
    ]


def interpolate(expression, variables=None):
    for name, value in {**VARIABLES, **(variables or {})}.items():
        expression = expression.replace("$" + name, value)
    if "$" in expression:
        raise ValueError(f"Unresolved dashboard variable: {expression}")
    return expression


class ControllerMetricQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.promtool = shutil.which(os.environ.get("PROMTOOL", "promtool"))
        if cls.promtool is None:
            raise RuntimeError(
                "promtool not found; install Prometheus's promtool or run "
                "make test-dashboard-queries PROMTOOL=/absolute/path/to/promtool"
            )
        cls.dashboard = json.loads((DASHBOARDS / "kgateway.json").read_text())

    def query(self, panel_id, ref_id="A", variables=None):
        panel = next(p for p in self.dashboard["panels"] if p["id"] == panel_id)
        target = next(t for t in panel["targets"] if t["refId"] == ref_id)
        return interpolate(target["expr"], variables)

    def run_promtool(self, command, document):
        # JSON is valid YAML, so fixtures need no third-party Python packages.
        with tempfile.TemporaryDirectory(prefix="dashboard-promql-") as directory:
            fixture = Path(directory) / "fixture.json"
            fixture.write_text(json.dumps(document))
            result = subprocess.run(
                [self.promtool, *command, str(fixture)],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_query(self, panel_id, series, expected, ref_id="A",
                     variables=None, eval_time="0m"):
        self.run_promtool(["test", "rules"], {
            "rule_files": [],
            "evaluation_interval": "1m",
            "tests": [{
                "interval": "1m",
                "input_series": series,
                "promql_expr_test": [{
                    "expr": self.query(panel_id, ref_id, variables),
                    "eval_time": eval_time,
                    "exp_samples": [
                        {"labels": label_set(labels), "value": value}
                        for labels, value in expected
                    ],
                }],
            }],
        })

    def test_state_gauges_do_not_multiply_by_replicas(self):
        for panel, metric, identity, output in STATE_GAUGES:
            for values, expected in (
                ((9, 9, 9), 9),
                ((9, 4, 0), 9),
                ((9,), 9),
                ((0, 0, 0), 0),
            ):
                with self.subTest(metric=metric, values=values):
                    self.assert_query(
                        panel, replicas(metric, values, **identity),
                        [(output, expected)],
                    )

    def test_state_gauges_without_samples_return_no_series(self):
        for panel, metric, _, _ in STATE_GAUGES:
            with self.subTest(metric=metric):
                self.assert_query(panel, [], [])

    def test_one_remaining_replica_preserves_state(self):
        for panel, metric, identity, output in STATE_GAUGES:
            with self.subTest(metric=metric):
                self.assert_query(
                    panel, replicas(metric, ("9 stale", "9 stale", "9 9"), **identity),
                    [(output, 9)], eval_time="1m",
                )

    def test_distinct_namespaces_remain_additive(self):
        for panel, metric, identity, output in STATE_GAUGES:
            with self.subTest(metric=metric):
                series = replicas(metric, (9, 9, 9), **identity)
                series += replicas(metric, (4, 4, 4), **{**identity, "namespace": "ns-b"})
                self.assert_query(panel, series, [(output, 13)])

    def test_exported_namespaces_remain_additive_after_scrape_label_collision(self):
        for panel, metric, identity, output in STATE_GAUGES:
            with self.subTest(metric=metric):
                target = {**identity, "namespace": "kgateway-system"}
                series = replicas(metric, (9, 9, 9), **{**target, "exported_namespace": "ns-a"})
                series += replicas(metric, (4, 4, 4), **{**target, "exported_namespace": "ns-b"})
                self.assert_query(panel, series, [(output, 13)],
                                  variables={"namespace": "kgateway-system"})

    def test_managed_resources_sum_distinct_parents(self):
        series = replicas("kgateway_resources_managed", (9, 9, 9),
                          namespace="ns-a", parent="gw-a", resource="HTTPRoute")
        series += replicas("kgateway_resources_managed", (4, 4, 4),
                           namespace="ns-a", parent="gw-b", resource="HTTPRoute")
        self.assert_query(6, series, [({"resource": "HTTPRoute"}, 13)])

    def test_distinct_resource_types_gateways_and_ports_stay_separate(self):
        for panel, metric, identity, output in STATE_GAUGES:
            changes = (
                ({"resource": "TCPRoute"},) if panel == 6 else
                ({"gateway": "gw-b"}, {"resource": "Cluster"}) if panel == 31 else
                ({"gateway": "gw-b"}, {"port": "443"})
            )
            for dimensions in changes:
                with self.subTest(metric=metric, dimensions=dimensions):
                    series = replicas(metric, (9, 9, 9), **identity)
                    series += replicas(metric, (4, 4, 4), **{**identity, **dimensions})
                    self.assert_query(panel, series, [(output, 9), ({**output, **dimensions}, 4)])

    def test_gateway_and_namespace_filters(self):
        for panel, metric, identity, output in STATE_GAUGES:
            gateway_label = "parent" if panel == 6 else "gateway"
            series = replicas(metric, (9, 9, 9), **identity)
            other_gateway = {**identity, gateway_label: "gw-b"}
            series += replicas(metric, (4, 4, 4), **other_gateway)
            series += replicas(metric, (2, 2, 2), **{**identity, "namespace": "ns-b"})
            with self.subTest(metric=metric, selection="single"):
                self.assert_query(panel, series, [(output, 9)],
                                  variables={"gateway": "gw-a", "namespace": "ns-a"})
            with self.subTest(metric=metric, selection="multiple gateways"):
                expected = (
                    [(output, 13)] if panel == 6 else
                    [(output, 9), ({**output, "gateway": "gw-b"}, 4)]
                )
                self.assert_query(panel, series, expected,
                                  variables={"gateway": "gw-a|gw-b", "namespace": "ns-a"})
            with self.subTest(metric=metric, selection="multiple namespaces"):
                self.assert_query(panel, series, [(output, 11)],
                                  variables={"gateway": "gw-a", "namespace": "ns-a|ns-b"})

    def test_managed_resources_keep_empty_parent_match(self):
        series = replicas("kgateway_resources_managed", (2, 2, 2),
                          namespace="ns-a", parent="", resource="BackendConfigPolicy")
        series += replicas("kgateway_resources_managed", (4, 4, 4),
                           namespace="ns-a", parent="gw-b", resource="HTTPRoute")
        self.assert_query(6, series, [({"resource": "BackendConfigPolicy"}, 2)],
                          variables={"gateway": "gw-a", "namespace": "ns-a"})

    def test_process_local_work_remains_additive(self):
        for panel, ref, metric, dimension, name, values, expected in (
            (7, "A", "kgateway_translator_translations_running", "translator", "gateway", (1, 2, 3), 6),
            (8, "A", "kgateway_controller_reconciliations_running", "controller", "gateway", (1, 2, 3), 6),
            (8, "B", "gloo_gateway_controller_reconciliations_running", "controller", "gateway", (1, 2, 3), 6),
            (9, "B", "kgateway_translator_translations_total", "translator", "gateway", (10, 20, 30), 60),
            (9, "A", "kgateway_controller_reconciliations_total", "controller", "gateway", (10, 20, 30), 60),
            (9, "C", "kgateway_status_syncer_status_syncs_total", "syncer", "status", (10, 20, 30), 60),
            (32, "A", "kgateway_resources_status_syncs_completed_total", "resource", "HTTPRoute", (10, 20, 30), 60),
        ):
            with self.subTest(metric=metric):
                labels = {"namespace": "ns-a", dimension: name}
                self.assert_query(panel, replicas(metric, values, **labels),
                                  [({dimension: name}, expected)], ref_id=ref)

    def test_process_local_operation_rates_remain_additive(self):
        for panel, ref, metric, dimension, name in (
            (17, "B", "kgateway_translator_translations_total", "translator", "gateway"),
            (18, "B", "kgateway_controller_reconciliations_total", "controller", "gateway"),
            (12, "A", "kgateway_status_syncer_status_syncs_total", "syncer", "status"),
        ):
            with self.subTest(metric=metric):
                labels = {"namespace": "ns-a", dimension: name, "result": "success"}
                series = replicas(metric, ("0+60x5", "0+120x5", "0+180x5"), **labels)
                self.assert_query(panel, series, [({dimension: name, "result": "success"}, 6)],
                                  ref_id=ref, eval_time="5m")

    def test_both_dashboards_have_valid_promql(self):
        rules = []
        for path in sorted(DASHBOARDS.glob("*.json")):
            dashboard = json.loads(path.read_text())
            for panel in dashboard["panels"]:
                for target in panel.get("targets", []):
                    if "expr" in target:
                        rules.append({
                            "record": f"dashboard:{path.stem}:panel_{panel['id']}:{target['refId']}",
                            "expr": interpolate(target["expr"]),
                        })
        self.assertTrue(rules)
        self.run_promtool(["check", "rules"], {
            "groups": [{"name": "dashboard-query-syntax", "rules": rules}],
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
