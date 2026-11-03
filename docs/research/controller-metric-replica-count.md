# Controller metrics and replica counts

[Issue #4](https://github.com/kgateway-dev/dashboards/issues/4) reports that nine HTTPRoutes appear as 27 with three controller replicas. The dashboard sums repeated observations of the same resource state. Deduplicate each logical state bucket before summing distinct buckets into the panel's existing grouping.

## Exporter evidence

The pinned kgateway v2.1.0 source (`919cae66255332a968f06f810bd2844bb817bbe0`) includes the legacy running and status metrics used by this dashboard.

| State gauge | Logical identity | Source behavior |
| --- | --- | --- |
| `kgateway_resources_managed` | `namespace, parent, resource` | [Declared as a gauge](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/krtcollections/metrics/metrics.go#L54-L62); [increments for each parent on resource addition](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/krtcollections/metrics/metrics.go#L570-L577). |
| `kgateway_xds_snapshot_resources` | `namespace, gateway, resource` | [Declared as a gauge](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/proxy_syncer/metrics.go#L65-L72); [sets snapshot item counts and zeroes deleted snapshots](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/proxy_syncer/perclient.go#L175-L230). |
| `kgateway_routing_domains` | `namespace, gateway, port` | [Declared as a gauge with a setter](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/translator/irtranslator/metrics.go#L11-L44); [sets the number of domains for a Gateway listener's bind port](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/translator/irtranslator/gateway.go#L162-L167). |

Current upstream source at `583dce389f3cc7e538a3d04bdee62bebfdedcf33` retains these identities for [managed resources](https://github.com/kgateway-dev/kgateway/blob/583dce389f3cc7e538a3d04bdee62bebfdedcf33/pkg/krtcollections/metrics/metrics.go#L52-L59), [snapshot resources](https://github.com/kgateway-dev/kgateway/blob/583dce389f3cc7e538a3d04bdee62bebfdedcf33/pkg/kgateway/proxy_syncer/metrics.go#L41-L48), and [routing domains](https://github.com/kgateway-dev/kgateway/blob/583dce389f3cc7e538a3d04bdee62bebfdedcf33/pkg/kgateway/translator/irtranslator/metrics.go#L25-L32).

## Corrected queries

Use an inner maximum over the complete exporter identity and an outer sum over the existing legend dimensions. Preserve `exported_namespace` as well as `namespace` to handle scrape-label collisions. This removes replica labels while retaining independent parents, namespaces, resource types, and ports. Prometheus documents [aggregation grouping and the behavior of `max` and `sum`](https://prometheus.io/docs/prometheus/latest/querying/operators/#aggregation-operators).

```promql
sum by (resource) (max by (namespace, exported_namespace, parent, resource) (kgateway_resources_managed{parent=~"$gateway|",namespace=~"$namespace|"}))
```

```promql
sum by (gateway, resource) (max by (namespace, exported_namespace, gateway, resource) (kgateway_xds_snapshot_resources{gateway=~"$gateway|",namespace=~"$namespace|"}))
```

```promql
sum by (gateway, port) (max by (namespace, exported_namespace, gateway, port) (kgateway_routing_domains{gateway=~"$gateway|",namespace=~"$namespace|"}))
```

For example, independent managed-resource buckets containing nine and four HTTPRoutes must total 13 regardless of replica count. Taking a maximum only by resource would instead select nine and discard the second bucket.

With Prometheus's default `honor_labels: false`, conflicting exporter labels become `exported_<label>` while target labels retain their names, as specified by the [scrape configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config). The kgateway [ServiceMonitor does not override `honorLabels`](https://github.com/kgateway-dev/kgateway/blob/583dce389f3cc7e538a3d04bdee62bebfdedcf33/install/helm/kgateway/templates/servicemonitor.yaml#L17-L25), and the Prometheus operator [adds the target's Kubernetes namespace](https://github.com/prometheus-operator/prometheus-operator/blob/v0.86.1/pkg/prometheus/promcfg.go#L2013-L2016). Thus `namespace` can identify the controller namespace and `exported_namespace` the resource namespace. Dropping the latter would merge independent resource namespaces and undercount them. Keeping both labels also works when `honor_labels: true` preserves exporter labels and `exported_namespace` is absent. Existing namespace-filter semantics are unchanged.

## Metrics that remain additive

- Running reconciliation and translation gauges represent process-local work. Their wrappers [increment on reconciliation entry and decrement on completion](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/controller/metrics.go#L54-L92), and do the same for [translations](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/translator/metrics/metrics.go#L66-L89). Keep their sums to report concurrent work across processes.
- Translation, reconciliation, and status-sync counters record operations performed by each process. The source increments [reconciliation counters](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/controller/metrics.go#L81-L86), [translation counters](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/translator/metrics/metrics.go#L85-L89), and [completed resource status syncs](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/krtcollections/metrics/metrics.go#L410-L420). Keep accumulated-counter sums and summed rates. Prometheus requires [calculating rates before aggregation](https://prometheus.io/docs/prometheus/latest/querying/functions/#rate) to detect individual counter resets.
- Duration histograms record individual operation observations. Keep summed rates before quantile calculation, as specified by the [histogram aggregation documentation](https://prometheus.io/docs/prometheus/latest/querying/functions/#histogram_quantile). Histogram format and unrelated label-filter changes are outside this fix.

## Limits

Managed-resource counts preserve the exporter's per-parent semantics. For HTTPRoutes, [each ParentRef contributes using its name and the route's namespace](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/krtcollections/metrics/metrics.go#L472-L479). One route attached to multiple parents can therefore contribute multiple times. There is no route-name label from which to recover a globally distinct object count. Gateway objects [use their own name as parent](https://github.com/kgateway-dev/kgateway/blob/919cae66255332a968f06f810bd2844bb817bbe0/internal/kgateway/krtcollections/metrics/metrics.go#L540-L544). The dashboard does not contain `kgateway_gateways`; the [official v2.1 metric inventory](https://github.com/kgateway-dev/kgateway.dev/blob/9bc0ecf1c1300d8f95b36398295f37083c234e58/assets/kgw-docs/snippets/2.1.x/metrics-control-plane.md) lists managed resources instead.

The grouping assumes one cluster/controller observation domain. Federated data with independent domains sharing exporter identities needs its actual cluster or controller-domain label retained in the inner maximum. This dashboard has no cluster selector; adding one is outside this fix. Default namespace collisions are covered; other custom relabeling requires checking the actual scraped labels when adapting these queries.

The maximum assumes replicas observe equivalent state. During convergence, a stale replica with a larger value can temporarily win. These queries remove duplicate state observations; they do not select an authoritative or newest observation.

## Local validation

The local query test target requires `python3` (standard library only) and `promtool` available on `PATH`. Validation used [Prometheus/promtool 3.5.0](https://github.com/prometheus/prometheus/releases/tag/v3.5.0).

```sh
make test-dashboard-queries
```

To select a particular executable:

```sh
make test-dashboard-queries PROMTOOL=/absolute/path/to/promtool
```

Prometheus supports synthetic input series and expression expectations through [`promtool test rules`](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/). The runner extracts expressions from the shipped dashboards and substitutes controlled Grafana variable values. It fails with installation instructions when `promtool` is missing. CI remains unchanged.

### Results on 2026-11-03

- Before the query changes, the regression suite failed in 25 subtests. Three replicas reporting nine resources produced 27, and distinct buckets containing nine and four produced 39 rather than 13. Query syntax and process-local operation checks already passed.
- After the changes, all 11 test methods passed: 47 PromQL result assertions plus syntax checks for all 43 expressions across both dashboards. Coverage includes replicas, distinct identity dimensions, single/multiple/all variable selections, empty parent, unequal and zero values, absent samples, stale replicas, additive work counters/gauges, and operation rates.
- `make analyze` passed Helm lint. Default, disabled, and custom-metadata chart rendering passed. Both rendered ConfigMaps contain the exact source dashboard JSON.
- `make package-charts TEST_ASSET_DIR="$PWD/_test/issue-4-chart"` succeeded. Both dashboard files in the package match the source bytes, and the packaged chart renders correctly.
- A comparison with the saved baseline confirmed exactly three controller expression changes. Other dashboard fields, all other expressions, the Envoy dashboard, CI, and the source chart version are unchanged.

### Independent review and additional verification

The second review found and corrected the default namespace-label collision described above. Its new regression failed for all three queries before `exported_namespace` was preserved. The expanded suite then passed all 12 test methods, including 50 PromQL result assertions and syntax checks for all 43 expressions. In-memory sensitivity checks also confirmed that the suite rejects both the original sums and a broad `max` that discards distinct identities.

A native Prometheus 3.5.0 server scraped three simulated controller endpoints on loopback, stored the samples, and evaluated queries extracted from the newly packaged chart's rendered ConfigMap. Both `honor_labels: false` and `true` passed the following checks: three replicas reporting nine yield nine instead of 27; two namespaces containing nine and four yield 13 instead of 39; namespace filters preserve existing behavior; one remaining replica preserves the correct result after stale-series removal; and process-local work/counters remain additive. This covers the local scrape-to-query pipeline without claiming to run a real kgateway controller or Grafana UI.

The independent review script and its results are under ignored `_test/issue-4-independent-review/`. Helm lint, chart rendering with default/disabled/custom values, package contents, and unchanged-file comparisons were checked again after the collision fix.

The official Linux AMD64 archive was checked against its release SHA-256 checksum (`e811827af26d822afb09a4f28314f61b618b12cff5369835a67f674d8b46f39a`). The downloaded executable and before/after logs are under ignored `_test/`; they are not part of the contribution. Rerun this checkout with:

```sh
make test-dashboard-queries PROMTOOL="$PWD/_test/tools/prometheus-3.5.0/promtool"
```

No live Grafana/Kubernetes environment was used, and no Git commands were run. These results establish local scrape/query behavior and chart integrity, not live deployment acceptance.
