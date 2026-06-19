import ifcopenshell
from neo4j import GraphDatabase

# Global lists to store nodes and relationships
nodes = []
relationships = []

def extract_ifc_hierarchy(ifc_file_path):
    """Extracts the spatial structure and elements from an IFC file."""
    ifc_file = ifcopenshell.open(ifc_file_path)

    structure = {"IfcProject": []}

    def extract_elements(parent_dict, ifc_object):
        """Recursively extracts elements and their relationships."""
        add_node(ifc_object.is_a(), ifc_object.GlobalId, ifc_object.Name)

        # Extract decomposition relationships (IfcRelAggregates)
        if hasattr(ifc_object, 'IsDecomposedBy'):
            for rel in ifc_object.IsDecomposedBy:
                for child in rel.RelatedObjects:
                    add_relationship(ifc_object.GlobalId, "Aggregates", child.GlobalId)
                    child_dict = create_element_dict(child, "Aggregates")
                    parent_dict["children"].append(child_dict)
                    extract_elements(child_dict, child)

        # Extract containment relationships (IfcRelContainedInSpatialStructure)
        if hasattr(ifc_object, 'ContainsElements'):
            for rel in ifc_object.ContainsElements:
                for child in rel.RelatedElements:
                    add_relationship(ifc_object.GlobalId, "ContainedIn", child.GlobalId)
                    child_dict = create_element_dict(child, "ContainedIn")
                    parent_dict["children"].append(child_dict)
                    extract_elements(child_dict, child)

        # Extract IfcRelFillsElement (doors/windows filling wall openings)
        if ifc_object.is_a('IfcWall') and hasattr(ifc_object, 'HasOpenings'):
            for rel in ifc_object.HasOpenings:
                opening = rel.RelatedOpeningElement
                if hasattr(opening, 'HasFillings'):
                    for filling in opening.HasFillings:
                        element = filling.RelatedBuildingElement
                        add_relationship(ifc_object.GlobalId, "FillsElement", element.GlobalId)
                        add_node(element.is_a(), element.GlobalId, element.Name)

        # Extract IfcRelSpaceBoundary relationships (space bounding elements)
        if ifc_object.is_a('IfcSpace') and hasattr(ifc_object, 'BoundedBy'):
            for rel in ifc_object.BoundedBy:
                related_element = rel.RelatedBuildingElement
                if related_element:
                    add_relationship(ifc_object.GlobalId, "BoundedBy", related_element.GlobalId)
                    add_node(related_element.is_a(), related_element.GlobalId, related_element.Name)

        # Extract IfcRelDefinesByProperties and IfcRelDefinesByType (property relationships)
        if hasattr(ifc_object, 'IsDefinedBy'):
            for rel in ifc_object.IsDefinedBy:
                if rel.is_a('IfcRelDefinesByProperties'):
                    property_set = rel.RelatingPropertyDefinition
                    add_relationship(ifc_object.GlobalId, "DefinesByProperties", property_set.GlobalId)
                    add_node(property_set.is_a(), property_set.GlobalId, property_set.Name)
                elif rel.is_a('IfcRelDefinesByType'):
                    element_type = rel.RelatingType
                    add_relationship(ifc_object.GlobalId, "DefinesByType", element_type.GlobalId)
                    add_node(element_type.is_a(), element_type.GlobalId, element_type.Name)

    # Iterate over all IfcProject entities
    for project in ifc_file.by_type("IfcProject"):
        project_dict = create_element_dict(project, None)
        extract_elements(project_dict, project)
        structure["IfcProject"].append(project_dict)

    return structure

def create_element_dict(ifc_object, relationship):
    """Helper function to create a dictionary for an IFC element."""
    return {
        "GlobalId": ifc_object.GlobalId,
        "name": ifc_object.Name,
        "type": ifc_object.is_a(),
        "relationship": relationship,
        "children": []
    }

def add_relationship(start_id, relationship, end_id):
    """Adds a relationship to the global relationships list."""
    if {"start": start_id, "relationship": relationship, "end": end_id} not in relationships:
        relationships.append({"start": start_id, "relationship": relationship, "end": end_id})

def add_node(label, global_id, name):
    """Adds a node to the global nodes list."""
    nodes.append({"label": label, "globalId": global_id, "name": name, "ifcType": label})

def update_neo4j(nodes, relationships, uri, user, password):
    """Updates the Neo4j database with extracted IFC data."""
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        session.execute_write(clear_database)
        for node in nodes:
            session.execute_write(create_node, node["label"], node["globalId"], node["name"], node["ifcType"])
        for relationship in relationships:
            session.execute_write(create_relationship, relationship["start"], relationship["relationship"], relationship["end"])
    driver.close()

def clear_database(tx):
    """Clears all nodes and relationships in the Neo4j database."""
    tx.run("MATCH (n) DETACH DELETE n")

def create_node(tx, label, global_id, name, ifc_type):
    """Creates a node in Neo4j."""
    query = """
    MERGE (n {globalId: $global_id})
    SET n.name = $name, n.ifcType = $ifc_type, n:%s
    """ % label
    tx.run(query, global_id=global_id, name=name, ifc_type=ifc_type)

def create_relationship(tx, start_id, relationship, end_id):
    """Creates a relationship in Neo4j."""
    query = """
    MATCH (a {globalId: $start_id}), (b {globalId: $end_id})
    MERGE (a)-[r:%s]->(b)
    """ % relationship
    tx.run(query, start_id=start_id, end_id=end_id)

# Example usage
ifc_file_path = r"d:/Projects/01-IFC_structure/IFC_files/Simple/Duplex_A_20110907.ifc"

ifc_hierarchy = extract_ifc_hierarchy(ifc_file_path)

# Update Neo4j
uri = "bolt://localhost:7687"
user = "neo4j"
password = "4262890Ab"  # Replace with your Neo4j password

update_neo4j(nodes, relationships, uri, user, password)
