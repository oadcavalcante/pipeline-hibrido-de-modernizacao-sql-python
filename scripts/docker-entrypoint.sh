#!/bin/sh
set -e

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  SQL Modernizer — use estes links no browser (não 0.0.0.0):"
echo "  🏠 Home:    http://127.0.0.1:8000"
echo "  📚 Docs:    http://127.0.0.1:8000/docs"
echo "  🎨 Studio:  https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8000"
echo "  📊 Langfuse: http://localhost:3000"
echo "════════════════════════════════════════════════════════════"
echo ""

exec langgraph dev --host 0.0.0.0 --port 8000 --no-browser
