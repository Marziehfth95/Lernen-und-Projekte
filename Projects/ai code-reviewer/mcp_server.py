import asyncio
import json
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import chromadb

# Logging einrichten
logging.basicConfig(level=logging.INFO)
logger= logging.getLogger("mcp_memory_server")

# 1. Wir benennen unseren MCP Server
app = Server("AI-Code-Review-Memory")

# 2. Wir verbinden uns mit der BEREITS EXISTIERENDEN Datenbank des Bots
chroma_client= chromadb.PersistentClient(path="./chroma_db")
memory_collection = chroma_client.get_or_create_collection(name="pr_reviews")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """Sagt der Claude Desktop App, welche Werkzeuge wir anbieten."""
    return [
        Tool(
            name="get_team_code_rules",
            description="Liest das konsolidierte Gedächtnis (Meta-Regeln und vergangene Fehler) des Code-Review-Bots aus der lokalen Vektordatenbank.",
            inputSchema={
                "type": "object",
                # Keine Parameter nötig
                "properties": {}, 
                "required": []
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Führt das Werkzeug aus, wenn Claude danach fragt."""
    if name == "get_team_code_rules":
        try:
            # Wir holen alles aus der Datenbank
            all_memories = memory_collection.get()
            documents = all_memories.get("documents", [])
            
            if not documents:
                return [TextContent(type="text", text="Die Datenbank ist leer. Das Team hat noch keine Fehler gemacht.")]
                
            # Wir geben es als formatierten JSON-String an Claude zurück
            return [TextContent(type="text", text=json.dumps(documents, indent=2))]
            
        except Exception as e:
            logger.error(f"Fehler beim Lesen der ChromaDB: {e}")
            return [TextContent(type="text", text=f"Datenbankfehler: {str(e)}")]
            
    raise ValueError(f"Unbekanntes Tool: {name}")

async def main():
    """Startet den MCP Server über STDIO (Standard für Claude Desktop)"""
    logger.info("Starte MCP Memory Server...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())