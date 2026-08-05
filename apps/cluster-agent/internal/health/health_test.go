package health

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthzAnswersAlive(t *testing.T) {
	server := New("127.0.0.1:0")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", recorder.Code)
	}
	if !strings.Contains(recorder.Body.String(), `"alive"`) {
		t.Fatalf("unexpected body: %s", recorder.Body.String())
	}
}

func TestNoOtherPathsExist(t *testing.T) {
	server := New("127.0.0.1:0")
	for _, path := range []string{"/", "/metrics", "/exec", "/debug/pprof/", "/config"} {
		recorder := httptest.NewRecorder()
		server.Handler().ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusNotFound {
			t.Fatalf("path %q must not exist, got %d", path, recorder.Code)
		}
	}
}

func TestNoMutatingMethods(t *testing.T) {
	server := New("127.0.0.1:0")
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete} {
		recorder := httptest.NewRecorder()
		server.Handler().ServeHTTP(recorder, httptest.NewRequest(method, "/healthz", nil))
		if recorder.Code == http.StatusOK {
			t.Fatalf("method %s must not be accepted on the probe endpoint", method)
		}
	}
}
