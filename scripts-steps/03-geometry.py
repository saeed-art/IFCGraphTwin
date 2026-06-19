import ifcopenshell
import ifcopenshell.geom
from neo4j import GraphDatabase

# List to store relationships globally
relationships = []

# List to store nodes globally
nodes = []

def extract_ifc_hierarchy(ifc_file_path):
    # Load the IFC file
    ifc_file = ifcopenshell.open(ifc_file_path)
    
    # Initialize the structure dictionary
    structure = {
        "IfcProject": []
    }
    
    # Geometry settings for ifcopenshell
    settings = ifcopenshell.geom.settings()
    
    # Helper function to extract elements recursively
    def extract_elements(parent_dict, ifc_object):
        if hasattr(ifc_object, 'IsDecomposedBy'):
            for rel in ifc_object.IsDecomposedBy:
                relationship_name = rel.is_a().replace('IfcRel', '').replace('Relationship', '')
                for child in rel.RelatedObjects:
                    child_dict = {
                        "GlobalId": child.GlobalId,
                        "name": child.Name,
                        "type": child.is_a(),
                        "relationship": relationship_name,
                        "children": []
                    }
                    parent_dict["children"].append(child_dict)
                    add_relationship(ifc_object.GlobalId, relationship_name, child.GlobalId)
                    extract_elements(child_dict, child)
        
        if hasattr(ifc_object, 'ContainsElements'):
            for rel in ifc_object.ContainsElements:
                relationship_name = rel.is_a().replace('IfcRel', '').replace('Relationship', '')
                for child in rel.RelatedElements:
                    child_dict = {
                        "GlobalId": child.GlobalId,
                        "name": child.Name,
                        "type": child.is_a(),
                        "relationship": relationship_name,
                        "children": []
                    }
                    parent_dict["children"].append(child_dict)
                    add_relationship(ifc_object.GlobalId, relationship_name, child.GlobalId)
                    extract_elements(child_dict, child)
        
        # Extract and add geometry data
        extract_and_store_geometry(ifc_object, parent_dict, settings)
        
    # Iterate over all IfcProject entities
    for project in ifc_file.by_type("IfcProject"):
        project_dict = {
            "GlobalId": project.GlobalId,
            "name": project.Name,
            "type": project.is_a(),
            "children": []
        }

        extract_elements(project_dict, project)
        structure["IfcProject"].append(project_dict)

    return structure

def extract_and_store_geometry(ifc_element, element_dict, settings):
    try:
        # Extract geometry using ifcopenshell
        shape = ifcopenshell.geom.create_shape(settings, ifc_element)
        vertices = shape.geometry.verts
        faces = shape.geometry.faces

        # Attach geometry data to the element
        element_dict["geometry"] = {
            "vertices": vertices,
            "faces": faces
        }

        # Add the element with geometry as a node
        add_node(element_dict["type"], element_dict["GlobalId"], element_dict["name"], element_dict["geometry"])

    except Exception as e:
        print(f"Error extracting geometry for {element_dict['type']} with GlobalId {element_dict['GlobalId']}: {e}")

def add_relationship(start_id, relationship, end_id):
    global relationships
    if {"start": start_id, "relationship": relationship, "end": end_id} not in relationships:
        relationships.append({"start": start_id, "relationship": relationship, "end": end_id})

def add_node(label, global_id, name, geometry=None):
    global nodes
    nodes.append({
        "label": label,
        "globalId": global_id,
        "name": name,
        "ifcType": label,
        "geometry": geometry
    })

def process_element(element, parent_id):
    add_node(element["type"], element["GlobalId"], element["name"], element.get("geometry"))
    if parent_id:
        add_relationship(parent_id, element["relationship"], element["GlobalId"])
    for child in element["children"]:
        process_element(child, element["GlobalId"])

def clear_database(tx):
    tx.run("MATCH (n) DETACH DELETE n")

def create_node(tx, label, global_id, name, ifc_type, geometry):
    query = """
    MERGE (n {globalId: $global_id})
    SET n.name = $name, n.ifcType = $ifc_type, n:%s, n.vertices = $vertices, n.faces = $faces
    """ % label
    tx.run(query, global_id=global_id, name=name, ifc_type=ifc_type, 
           vertices=geometry.get("vertices") if geometry else None,
           faces=geometry.get("faces") if geometry else None)

def create_relationship(tx, start_id, relationship, end_id):
    query = """
    MATCH (a {globalId: $start_id}), (b {globalId: $end_id})
    MERGE (a)-[r:%s]->(b)
    """ % relationship
    tx.run(query, start_id=start_id, end_id=end_id)

def update_neo4j(nodes, relationships, uri, user, password):
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        # Clear the existing database
        session.write_transaction(clear_database)
        
        # Create nodes
        for node in nodes:
            session.write_transaction(create_node, node["label"], node["globalId"], node["name"], node["ifcType"], node.get("geometry"))
        
        # Create relationships
        for relationship in relationships:
            session.write_transaction(create_relationship, relationship["start"], relationship["relationship"], relationship["end"])

    driver.close()

# Example usage
ifc_file_path = r"D:\Ger-2nd-Semester\SoftwareLab\VS\IFC_PY_001\IFC files\SCG_AR_optimized_2-withPositionCorrection.ifc"

ifc_hierarchy = extract_ifc_hierarchy(ifc_file_path)

for project in ifc_hierarchy["IfcProject"]:
    process_element(project, None)

# Update Neo4j
uri = "bolt://localhost:7687"  # Adjust to your Neo4j instance
user = "neo4j"
password = "123456789A"  # Replace with your Neo4j password

update_neo4j(nodes, relationships, uri, user, password)
