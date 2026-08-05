// Package collector defines the read-only collection boundaries.
//
// Sprint 0 ships interfaces and the registry guard only — no Kubernetes
// client exists yet. The registry enforces the v1 security contract: any
// collector declaring a forbidden resource kind is rejected at registration,
// so a violation fails unit tests and startup, never review.
package collector

import (
	"context"
	"fmt"
	"strings"
)

// ResourceKind names a Kubernetes resource kind a collector observes.
type ResourceKind string

// ForbiddenKinds are resources the agent must never observe or request.
var ForbiddenKinds = map[ResourceKind]string{
	"secrets":                       "Kubernetes Secrets are never read by the agent",
	"configmaps/data":               "ConfigMap data is never collected (metadata at most)",
	"pods/exec":                     "exec is never used",
	"pods/attach":                   "attach is never used",
	"pods/portforward":              "portforward is never used",
	"tokenreviews":                  "token review is never requested",
	"subjectaccessreviews":          "subject access review is never requested",
	"*":                             "wildcard resources are never requested",
	"mutatingwebhookconfigurations": "write-path resources are out of scope",
}

// AllowedVerbs is the complete verb set available to any collector.
var AllowedVerbs = []string{"get", "list", "watch"}

// Event is a normalized observation emitted by a collector.
type Event struct {
	Kind      ResourceKind
	Namespace string
	Name      string
	UID       string
	// Status carries selected, bounded status fields — never full manifests.
	Status map[string]string
}

// Collector observes one resource family, read-only.
type Collector interface {
	// Name is a unique, stable identifier.
	Name() string
	// Kinds lists every resource kind this collector observes.
	Kinds() []ResourceKind
	// Collect performs one bounded observation pass.
	Collect(ctx context.Context) ([]Event, error)
}

// Registry holds registered collectors and enforces the security contract.
type Registry struct {
	collectors map[string]Collector
}

func NewRegistry() *Registry {
	return &Registry{collectors: map[string]Collector{}}
}

// Register adds a collector, rejecting duplicates and forbidden kinds.
func (r *Registry) Register(c Collector) error {
	name := c.Name()
	if name == "" {
		return fmt.Errorf("collector name is required")
	}
	if _, exists := r.collectors[name]; exists {
		return fmt.Errorf("collector %q is already registered", name)
	}
	for _, kind := range c.Kinds() {
		normalized := ResourceKind(strings.ToLower(strings.TrimSpace(string(kind))))
		if reason, forbidden := ForbiddenKinds[normalized]; forbidden {
			return fmt.Errorf("collector %q declares forbidden kind %q: %s", name, kind, reason)
		}
		if strings.Contains(string(normalized), "*") {
			return fmt.Errorf("collector %q declares wildcard kind %q", name, kind)
		}
	}
	r.collectors[name] = c
	return nil
}

// Names returns the registered collector names.
func (r *Registry) Names() []string {
	names := make([]string, 0, len(r.collectors))
	for name := range r.collectors {
		names = append(names, name)
	}
	return names
}
