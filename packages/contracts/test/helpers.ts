/** Shared test helpers (not a test file). */

export function manifestWithSelectorValue(value: string): string {
  return `apiVersion: drake.duosis.com/v1alpha1
kind: ProjectObservability
metadata: {name: alpha}
spec:
  repository: {provider: github, owner: example-org, name: alpha}
  owners: [{team: platform}]
  environments:
    - {name: dev, runtime: kubernetes, branch: dev, clusterRef: cluster-a, namespace: alpha-dev, criticality: medium}
  services:
    - name: api
      component: api
      runtime: fastapi
      metricsProfile: fastapi-v1
      workloadSelector:
        value: ${JSON.stringify(value)}
  tenantModel: {mode: none}
`;
}
