package boards

import (
	_ "embed"
	"encoding/json"
	"regexp"
	"strings"
)

//go:embed board_directory.json
var data []byte

type Board struct {
	ID, Name string
	Tier     int
	Aliases  []string
}

var index map[string]Board
var nonID = regexp.MustCompile(`[^a-z0-9]+`)

func init() {
	var directory struct {
		Boards []struct {
			ID      string   `json:"id"`
			Name    string   `json:"name"`
			Tier    int      `json:"tier"`
			Aliases []string `json:"aliases"`
		} `json:"boards"`
	}
	if err := json.Unmarshal(data, &directory); err != nil {
		panic(err)
	}
	index = map[string]Board{}
	for _, raw := range directory.Boards {
		b := Board{raw.ID, raw.Name, raw.Tier, raw.Aliases}
		for _, key := range append([]string{b.ID, b.Name}, b.Aliases...) {
			index[normalize(key)] = b
		}
	}
}

func normalize(v string) string {
	return strings.Trim(nonID.ReplaceAllString(strings.ToLower(v), "-"), "-")
}
func Resolve(v string) (Board, bool) { b, ok := index[normalize(v)]; return b, ok }
