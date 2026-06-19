#!/usr/bin/env python3
"""Extract IFC spatial structure and load into Neo4j.

Usage:
  python scripts/extract_ifc_to_neo4j.py --ifc path/to/model.ifc --uri bolt://localhost:7687 --user neo4j --password test --clear

Requirements:
  pip install ifcopenshell neo4j
"""
import argparse
import ifcopenshell
from neo4j import GraphDatabase
import sys

# Local settings - edit as needed
IFC_PATH = "d:/Projects/01-IFC_structure/IFC_files/Simple/Duplex_A_20110907.ifc"
NEO4J_URI = "bolt://localhost:7687"
# NEO4J_URI = "neo4j://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "4262890Ab"  # <-- UPDATE THIS
CLEAR_DB = True


def get_gid(e):
    return getattr(e, 'GlobalId', None) or getattr(e, 'globalId', None)


def get_name(e):
    name = getattr(e, 'Name', None)
    if name:
        return str(name)
    lname = getattr(e, 'LongName', None)
    if lname:
        return str(lname)
    return None


def collect_spatial(ifc):
    nodes = {}
    rels = []

    # Types that make up the spatial structure
    spatial_types = ['IfcProject', 'IfcSite', 'IfcBuilding', 'IfcBuildingStorey', 'IfcSpace']

    # Collect only spatial structure objects
    for ent_type in spatial_types:
        for ent in ifc.by_type(ent_type):
            gid = get_gid(ent)
            if not gid:
                continue
            nodes[gid] = {
                'globalId': gid,
                'entity': ent.is_a(),
                'name': get_name(ent) or ent.is_a()
            }

    # Spatial aggregation relationships (project->site->building->storey->...)
    for rel in ifc.by_type('IfcRelAggregates'):
        parent = rel.RelatingObject
        children = rel.RelatedObjects
        pg = get_gid(parent)
        if not pg or pg not in nodes:
            continue
        for c in children:
            cg = get_gid(c)
            if not cg or cg not in nodes:
                continue
            rels.append((pg, cg, 'AGGREGATES'))

    return nodes, rels


def load_into_neo4j(uri, user, password, nodes, rels, clear=False):
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        if clear:
            session.run("MATCH (n) DETACH DELETE n")

        tx = session.begin_transaction()
        for n in nodes.values():
            # Use ONLY the specific IFC class as the label so Neo4j auto-colors them differently
            entity_label = n['entity']
            tx.run(
                f"MERGE (x:{entity_label} {{globalId:$gid}}) SET x.name=$name, x.entity=$entity",
                gid=n['globalId'], name=n['name'], entity=n['entity']
            )

        for p, c, rel_type in rels:
            tx.run(
                "MATCH (a {globalId:$p}), (b {globalId:$c}) MERGE (a)-[r:REL {type:$rtype}]->(b)",
                p=p, c=c, rtype=rel_type
            )

        tx.commit()
    driver.close()


def main():
    parser = argparse.ArgumentParser(description='Extract IFC spatial structure to Neo4j')
    parser.add_argument('--ifc', default=IFC_PATH, help='Path to IFC file')
    parser.add_argument('--uri', default=NEO4J_URI, help='Neo4j bolt URI')
    parser.add_argument('--user', default=NEO4J_USER, help='Neo4j username')
    parser.add_argument('--password', default=NEO4J_PASSWORD, help='Neo4j password')
    parser.add_argument('--clear', action='store_true', default=CLEAR_DB, help='Clear Neo4j database before import')

    args = parser.parse_args()

    try:
        ifc = ifcopenshell.open(args.ifc)
    except Exception as e:
        print('Failed to open IFC file:', e)
        sys.exit(1)

    print('Collecting spatial structure from IFC...')
    nodes, rels = collect_spatial(ifc)
    print(f'Found {len(nodes)} nodes and {len(rels)} relationships')

    print('Loading into Neo4j...')
    load_into_neo4j(args.uri, args.user, args.password, nodes, rels, clear=args.clear)
    print('\nDone! Extract and load complete.')
    print('-' * 60)
    print('To view your graph in Neo4j Browser (usually http://localhost:7474):')
    print('1. Log in with the same username and password.')
    print('2. Run this Cypher query to see your spatial structure:')
    print('   MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 150')
    print('-' * 60)


if __name__ == '__main__':
    main()
