package auth

import "errors"

// Principal is an API key and the model names it may call.
type Principal struct {
	Secret string
	Scopes map[string]struct{}
}

// Authenticator rejects missing keys, unknown keys, and out-of-scope models.
type Authenticator struct {
	keys map[string]Principal
}

func New(keys []Principal) *Authenticator {
	m := make(map[string]Principal, len(keys))
	for _, key := range keys {
		m[key.Secret] = key
	}
	return &Authenticator{keys: m}
}

// Check returns 200, 401, or 403.
func (a *Authenticator) Check(secret, model string) (int, error) {
	if secret == "" {
		return 401, errors.New("missing api key")
	}
	principal, ok := a.keys[secret]
	if !ok {
		return 401, errors.New("unknown api key")
	}
	if _, ok := principal.Scopes[model]; !ok {
		return 403, errors.New("model is out of scope")
	}
	return 200, nil
}
