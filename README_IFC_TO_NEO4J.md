**IFC → Neo4j**

- **Purpose**: Extract spatial structure from an IFC file and import it into Neo4j as a graph.

- **Script**: [scripts/extract_ifc_to_neo4j.py](scripts/extract_ifc_to_neo4j.py)

Quick setup

1. Install Python packages:

```bash
pip install ifcopenshell neo4j
```

2. Start Neo4j (quick Docker example):

```bash
docker run -d --name neo4j -p7474:7474 -p7687:7687 -e NEO4J_AUTH=neo4j/test neo4j:5
```

3. Import an IFC and load graph:

```bash
python scripts/extract_ifc_to_neo4j.py --ifc IFC_files/Complex/SCG_AR.ifc --uri bolt://localhost:7687 --user neo4j --password test --clear
```

4. Connect from VS Code:

- Install a Neo4j extension (e.g., Neo4j Explorer / Neo4j Browser) and create a connection using `bolt://localhost:7687` with the same credentials.
- Open the DB view and run Cypher like:

```cypher
MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 100
```

Notes

- The script creates nodes labelled `Ifc` with properties `globalId`, `name`, and `entity` (the IFC type). Relationships are created as `REL` with a `type` property such as `AGGREGATES` or `CONTAINS`.
- If your model is large, consider modifying the script to batch writes or to add more properties you need (e.g., object type, bounding boxes).

If you want, I can run a quick import for one of the IFC files in `IFC_files/Complex/` (you'll need Neo4j running and to confirm credentials). 
