// Package kube builds the read-only Kubernetes clients.
//
// In-cluster configuration is the default; an explicit kubeconfig path is
// accepted only for local development and disposable test clusters. There
// is no write client anywhere in this package.
package kube

import (
	"fmt"

	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"

	"github.com/Duosis-Developer-Team/Drake/apps/cluster-agent/internal/engine"
)

// Clients bundles the dynamic reader and the CRD presence check.
type Clients struct {
	Dynamic    dynamic.Interface
	CRDPresent engine.CRDPresent
}

// New builds clients from in-cluster config, or from kubeconfigPath when
// set (local/test only). QPS is bounded so the agent can never stampede
// the API server.
func New(kubeconfigPath string) (*Clients, error) {
	var restConfig *rest.Config
	var err error
	if kubeconfigPath != "" {
		restConfig, err = clientcmd.BuildConfigFromFlags("", kubeconfigPath)
	} else {
		restConfig, err = rest.InClusterConfig()
	}
	if err != nil {
		return nil, fmt.Errorf("kubernetes config: %w", err)
	}
	restConfig.QPS = 20
	restConfig.Burst = 40

	dynamicClient, err := dynamic.NewForConfig(restConfig)
	if err != nil {
		return nil, fmt.Errorf("dynamic client: %w", err)
	}
	discoveryClient, err := discovery.NewDiscoveryClientForConfig(restConfig)
	if err != nil {
		return nil, fmt.Errorf("discovery client: %w", err)
	}
	return &Clients{
		Dynamic:    dynamicClient,
		CRDPresent: crdChecker(discoveryClient),
	}, nil
}

// crdChecker reports whether an optional group/version serves a resource.
// Any error is treated as absent — the agent never guesses a collection
// into existence.
func crdChecker(client discovery.DiscoveryInterface) engine.CRDPresent {
	return func(gvr schema.GroupVersionResource) bool {
		resources, err := client.ServerResourcesForGroupVersion(
			gvr.GroupVersion().String(),
		)
		if err != nil || resources == nil {
			return false
		}
		for _, resource := range resources.APIResources {
			if resource.Name == gvr.Resource {
				return true
			}
		}
		return false
	}
}
