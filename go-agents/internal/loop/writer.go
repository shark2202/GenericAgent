package loop

import "os"

// logWriter 默认写 stderr（不污染 stdout demo 输出）。
type stderrWriter struct{ f *os.File }

func (w *stderrWriter) Write(p []byte) (int, error) { return w.f.Write(p) }
func newDefaultWriter() *stderrWriter                { return &stderrWriter{f: os.Stderr} }
