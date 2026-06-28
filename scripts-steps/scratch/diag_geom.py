import psycopg2
conn = psycopg2.connect('host=localhost port=5432 dbname=postgis_ifc user=postgres password=4262890')
cur = conn.cursor()
cur.execute("SELECT global_id, ST_GeometryType(geom), left(ST_AsText(geom), 200) FROM ifc_geometry WHERE ifc_type='IfcWall' LIMIT 1")
row = cur.fetchone()
print('GlobalId:', row[0])
print('GeomType:', row[1])
print('WKT sample:', row[2])
conn.close()
