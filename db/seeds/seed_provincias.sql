-- seed_provincias.sql
-- 24 Argentine provinces with ISO 3166-2 code and region

INSERT INTO dim_provincia (codigo_iso, nombre, region) VALUES
    -- AMBA
    ('AR-C', 'Ciudad Autónoma de Buenos Aires', 'AMBA'),

    -- Pampeana
    ('AR-B', 'Buenos Aires',                    'Pampeana'),
    ('AR-X', 'Córdoba',                         'Pampeana'),
    ('AR-E', 'Entre Ríos',                      'Pampeana'),
    ('AR-L', 'La Pampa',                        'Pampeana'),
    ('AR-S', 'Santa Fe',                        'Pampeana'),

    -- NOA
    ('AR-K', 'Catamarca',                       'NOA'),
    ('AR-Y', 'Jujuy',                           'NOA'),
    ('AR-F', 'La Rioja',                        'NOA'),
    ('AR-A', 'Salta',                           'NOA'),
    ('AR-G', 'Santiago del Estero',             'NOA'),
    ('AR-T', 'Tucumán',                         'NOA'),

    -- NEA
    ('AR-H', 'Chaco',                           'NEA'),
    ('AR-W', 'Corrientes',                      'NEA'),
    ('AR-P', 'Formosa',                         'NEA'),
    ('AR-N', 'Misiones',                        'NEA'),

    -- Cuyo
    ('AR-M', 'Mendoza',                         'Cuyo'),
    ('AR-J', 'San Juan',                        'Cuyo'),
    ('AR-D', 'San Luis',                        'Cuyo'),

    -- Patagonia
    ('AR-U', 'Chubut',                          'Patagonia'),
    ('AR-Q', 'Neuquén',                         'Patagonia'),
    ('AR-R', 'Río Negro',                       'Patagonia'),
    ('AR-Z', 'Santa Cruz',                      'Patagonia'),
    ('AR-V', 'Tierra del Fuego',                'Patagonia')

ON CONFLICT (codigo_iso) DO NOTHING;
