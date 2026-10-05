-- seed_producto_overrides.sql
-- Curated product attribute overrides (migration 039). This file is the source
-- of truth: every migration run replaces producto_overrides with these rows,
-- so edit here, never in the database.
--
-- Add a row only when the chain vote is wrong; find candidates with
--   SELECT * FROM v_producto_conflictos_pendientes ORDER BY cadenas_fuente DESC;
-- Leave a column NULL to keep the voted value. contenido and contenido_unidad
-- (g, ml or u) go together; tipo and variante use the dictionary values
-- (seed_producto_tipos.sql, seed_producto_variantes.sql). nota says why the
-- vote is wrong and what was checked.
--
-- The rows below come from the review of the 500 products sold by the most
-- chains (run of 2026-03-24), checked against every chain's description.

BEGIN;

DELETE FROM producto_overrides;

INSERT INTO producto_overrides
    (producto_identity, descripcion, marca, contenido, contenido_unidad, tipo, variante, nota)
VALUES
    ('ean:7622201745561', NULL, 'TERRABUSI', NULL, NULL, NULL, NULL, 'Brand VARIEDAD (6 chains) vs TERRABUSI (2); Variedad is the Terrabusi line, as chains 9, 12 and 13 describe it'),
    ('ean:7790550000157', NULL, 'LA PLANTA DE CAFE', NULL, NULL, NULL, NULL, 'Seven spellings of the brand tied; every chain description names La Planta de Cafe'),
    ('ean:7790550000164', NULL, 'LA PLANTA DE CAFE', NULL, NULL, NULL, NULL, 'Seven spellings of the brand tied; every chain description names La Planta de Cafe'),
    ('ean:7790742167101', NULL, 'FINLANDIA', NULL, NULL, NULL, NULL, 'Brand tied 2-2 with the maker LA SERENISIMA; six of seven chain descriptions name Finlandia'),
    ('ean:7891000248799', NULL, 'KIT KAT', NULL, NULL, NULL, NULL, 'KITKAT (4 chains) vs KIT KAT (3); the brand is written Kit Kat'),
    ('ean:7790070035936', NULL, 'GRANJA DEL SOL', NULL, NULL, 'bocaditos de pollo', NULL, 'Brand tied between the line PATITAS and its maker; chains describe chicken bites, no type term'),
    ('ean:7797394001265', 'PASTILLAS TIC TAC MIX DE FRUTAS X 16 GR', NULL, NULL, NULL, NULL, NULL, 'The winning description repeats itself; the other chains agree on a 16 g fruit mix'),
    ('ean:7791290795778', NULL, NULL, 360, 'ml', NULL, NULL, 'Size tied 2-2 between 260 g and 360 ml; chains 2, 10 and 13 describe 360 cm3'),
    ('ean:7791290795792', NULL, NULL, 360, 'ml', NULL, NULL, 'Size tied 2-2 between 260 g and 360 ml; descriptions say 360 cm3'),
    ('ean:7791290794108', NULL, NULL, NULL, NULL, NULL, 'lima', 'Chain 24 alone says C G LIMON VERDE; every other chain says lima'),
    ('ean:7790895643842', NULL, NULL, NULL, NULL, 'bebida de soja', NULL, 'Chain descriptions say BEBIDA DE SOJA; Ades is not a juice'),
    ('ean:7790895643743', NULL, NULL, NULL, NULL, 'bebida de soja', NULL, 'Chain descriptions say BEBIDA DE SOJA; Ades is not a juice'),
    ('ean:7790895643835', NULL, NULL, NULL, NULL, 'bebida de soja', NULL, 'Chain descriptions say BEBIDA DE SOJA; Ades is not a juice'),
    ('ean:7790895643866', NULL, NULL, NULL, NULL, 'bebida de soja', NULL, 'Chain descriptions say BEBIDA DE SOJA; Ades is not a juice'),
    ('ean:7791290792043', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Chain descriptions say jabon ropa or diluible'),
    ('ean:7791290792012', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Chain descriptions say para ropa or matic'),
    ('ean:7791290792036', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Chain descriptions say jabon ropa'),
    ('ean:7791290792104', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Chain descriptions say jabon ropa en polvo matic'),
    ('ean:7791290792814', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Chain descriptions say jabon liquido para ropa'),
    ('ean:7791290795495', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Chain descriptions say jabon diluible for clothes'),
    ('ean:7791290796409', NULL, NULL, NULL, NULL, 'jabón para ropa', NULL, 'Skip liquid laundry soap in every chain description'),
    ('ean:7790770601899', NULL, NULL, NULL, NULL, 'toallas femeninas', NULL, 'Chains write TOALLA, TOALLLA or TH; no type term matches'),
    ('ean:7790770601912', NULL, NULL, NULL, NULL, 'toallas femeninas', NULL, 'Chains write TOALLA, TOALL FEMENINA or TH; no type term matches'),
    ('ean:4005900985828', NULL, NULL, NULL, NULL, 'toallitas desmaquillantes', NULL, 'Every chain describes makeup remover wipes; no type term matches'),
    ('ean:7790064001909', NULL, NULL, NULL, NULL, 'algodón', NULL, 'Chains describe cotton makeup remover pads; no type term matches'),
    ('ean:7790580131487', NULL, NULL, NULL, NULL, 'baño de repostería', NULL, 'Every chain describes a baño de reposteria; no type term matches');

COMMIT;
