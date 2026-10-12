package auth

import "testing"

func TestCheckScopes(t *testing.T) {
	a := New([]Principal{
		{Secret: "rank", Scopes: map[string]struct{}{"ranking": {}}},
		{Secret: "both", Scopes: map[string]struct{}{"ranking": {}, "text": {}}},
	})
	cases := []struct {
		secret string
		model  string
		want   int
	}{
		{"", "ranking", 401},
		{"nope", "ranking", 401},
		{"rank", "text", 403},
		{"rank", "ranking", 200},
		{"both", "text", 200},
		{"both", "ranking", 200},
	}
	for _, tc := range cases {
		got, err := a.Check(tc.secret, tc.model)
		if got != tc.want {
			t.Fatalf("Check(%q, %q) = %d, want %d", tc.secret, tc.model, got, tc.want)
		}
		if tc.want == 200 && err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if tc.want != 200 && err == nil {
			t.Fatalf("expected error for %q %q", tc.secret, tc.model)
		}
	}
}
