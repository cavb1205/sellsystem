import ast
from pathlib import Path
import tempfile
from django.test import SimpleTestCase
from ops.preparar_claves_sesiones import preparar


class PrepararClavesTests(SimpleTestCase):
    def test_copia_privada_conserva_otras_variables_y_no_muestra_secretos(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'origen.py';destination=Path(folder)/'candidata.py'
            original="# Configuración ficticia con tildes\nKEY = 'antigua-ficticia'; BOT_TOKEN = 'bot-ficticio'\nPASSWORD_BD = 'bd-ficticia'\n"
            source.write_text(original);source.chmod(0o600)
            result=preparar(source,destination)
            self.assertEqual(source.read_text(),original)
            values={n.targets[0].id:ast.literal_eval(n.value)
                    for n in ast.parse(destination.read_text()).body if isinstance(n,ast.Assign)}
            self.assertEqual(values['BOT_TOKEN'],'bot-ficticio')
            self.assertEqual(values['PASSWORD_BD'],'bd-ficticia')
            self.assertGreaterEqual(len(values['KEY']),80)
            self.assertGreaterEqual(len(values['JWT_SIGNING_KEY']),80)
            self.assertNotEqual(values['KEY'],values['JWT_SIGNING_KEY'])
            self.assertEqual(destination.stat().st_mode&0o777,0o600)
            self.assertNotIn(values['KEY'],str(result))
            self.assertNotIn(values['JWT_SIGNING_KEY'],str(result))

    def test_no_sobrescribe_archivos_existentes_ni_el_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'origen.py';source.write_text("KEY='solo-ficticia'\n");source.chmod(0o600)
            with self.assertRaises(ValueError):preparar(source,source)
            destination=Path(folder)/'existente.py';destination.write_text('no cambiar')
            with self.assertRaises(FileExistsError):preparar(source,destination)
            self.assertEqual(destination.read_text(),'no cambiar')

    def test_origen_publico_o_clave_ambigua_no_crea_copia(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'origen.py';destination=Path(folder)/'destino.py'
            source.write_text("KEY='ficticia'\n");source.chmod(0o644)
            with self.assertRaises(ValueError):preparar(source,destination)
            source.write_text("KEY='uno'\nKEY='dos'\n");source.chmod(0o600)
            with self.assertRaises(ValueError):preparar(source,destination)
            self.assertFalse(destination.exists())

    def test_error_de_sintaxis_no_muestra_la_linea_privada(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'origen.py';destination=Path(folder)/'destino.py'
            source.write_text("KEY='valor-ficticio-no-mostrar\n");source.chmod(0o600)
            with self.assertRaises(ValueError) as result:preparar(source,destination)
            self.assertNotIn('valor-ficticio',str(result.exception))
            self.assertTrue(result.exception.__suppress_context__)
            self.assertFalse(destination.exists())
