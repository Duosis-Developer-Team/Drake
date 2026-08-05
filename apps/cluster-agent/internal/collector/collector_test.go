package collector

import (
	"context"
	"strings"
	"testing"
)

type fakeCollector struct {
	name  string
	kinds []ResourceKind
}

func (f fakeCollector) Name() string                               { return f.name }
func (f fakeCollector) Kinds() []ResourceKind                      { return f.kinds }
func (f fakeCollector) Collect(_ context.Context) ([]Event, error) { return nil, nil }

func TestRegisterAllowedCollector(t *testing.T) {
	r := NewRegistry()
	err := r.Register(fakeCollector{name: "workloads", kinds: []ResourceKind{"deployments", "pods"}})
	if err != nil {
		t.Fatalf("expected allowed collector to register, got %v", err)
	}
	if len(r.Names()) != 1 {
		t.Fatalf("expected one collector, got %v", r.Names())
	}
}

func TestRejectSecretsCollector(t *testing.T) {
	r := NewRegistry()
	err := r.Register(fakeCollector{name: "bad", kinds: []ResourceKind{"secrets"}})
	if err == nil {
		t.Fatal("expected registration of a secrets collector to fail")
	}
	if !strings.Contains(err.Error(), "never read") {
		t.Fatalf("expected boundary reason in error, got %v", err)
	}
}

func TestRejectExecAttachPortforward(t *testing.T) {
	r := NewRegistry()
	for _, kind := range []ResourceKind{"pods/exec", "pods/attach", "pods/portforward"} {
		if err := r.Register(fakeCollector{name: string(kind), kinds: []ResourceKind{kind}}); err == nil {
			t.Fatalf("expected %q to be rejected", kind)
		}
	}
}

func TestRejectWildcard(t *testing.T) {
	r := NewRegistry()
	if err := r.Register(fakeCollector{name: "wild", kinds: []ResourceKind{"*"}}); err == nil {
		t.Fatal("expected wildcard kind to be rejected")
	}
	if err := r.Register(fakeCollector{name: "wild2", kinds: []ResourceKind{"apps/*"}}); err == nil {
		t.Fatal("expected partial wildcard kind to be rejected")
	}
}

func TestRejectDuplicateName(t *testing.T) {
	r := NewRegistry()
	first := fakeCollector{name: "workloads", kinds: []ResourceKind{"deployments"}}
	if err := r.Register(first); err != nil {
		t.Fatal(err)
	}
	if err := r.Register(first); err == nil {
		t.Fatal("expected duplicate registration to fail")
	}
}

func TestAllowedVerbsAreReadOnly(t *testing.T) {
	for _, verb := range AllowedVerbs {
		switch verb {
		case "get", "list", "watch":
		default:
			t.Fatalf("verb %q violates the read-only contract", verb)
		}
	}
	if len(AllowedVerbs) != 3 {
		t.Fatalf("allowed verbs must be exactly get/list/watch, got %v", AllowedVerbs)
	}
}
