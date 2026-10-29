# e-CF DGII para Odoo: facturación electrónica en República Dominicana

Módulo de **facturación electrónica para Odoo en República Dominicana**. Envía a
la **DGII** (Dirección General de Impuestos Internos) las facturas que publicas
en Odoo como **comprobantes fiscales electrónicos (e-CF)**, tal como exige la
**Ley núm. 32-23 de Facturación Electrónica** y su reglamento, el **Decreto
587-24**.

Funciona en **Odoo 17 Community y Enterprise**, sobre la localización
dominicana `l10n_do_accounting`. La firma y el envío a la DGII los hace
[GLECF](https://glecf.com). Tu certificado digital se sube desde Odoo, pero
queda guardado en GLECF, no en Odoo.

## Qué hace

- Envía los **diez tipos de e-CF** de la DGII:
  - de venta: E31 Crédito Fiscal, E32 Consumo, E33 Nota de Débito, E34 Nota de
    Crédito, E44 Regímenes Especiales, E45 Gubernamental y E46 Exportaciones;
  - desde la factura de proveedor: E41 Compras, E43 Gastos Menores y E47 Pagos
    al Exterior, con las retenciones de ITBIS e ISR que tengas en Odoo.
- Revisa la factura antes de enviarla: si falta el RNC del cliente o la fecha de
  vencimiento de la secuencia, lo dice en Odoo, no en un rechazo de la DGII.
- Reúne en una sola lista las **secuencias e-NCF** de todos los tipos, con su
  último número y su vencimiento, y avisa de las fechas que nadie ha revisado.
- Imprime la factura con lo que pide la DGII: código QR de consulta, código de
  seguridad y fecha y hora de la firma digital. Guarda el XML firmado.
- Usa los datos de tu empresa en Odoo: nombre, RNC y dirección van al registro
  de GLECF y a cada e-CF.
- Sube tu certificado digital a GLECF desde los Ajustes de Odoo.
- Muestra en Ajustes tu plan de GLECF y cuántos comprobantes te quedan este mes.
- Muestra el estado ante la DGII en cada factura y en el listado.
- No deja volver a borrador una factura ya enviada a la DGII.

Todo en pesos dominicanos (DOP). Las facturas en otra moneda llegarán en una
próxima versión.

## Instalación

Necesitas Odoo 17 y la localización dominicana:

```bash
git clone -b 17.0 https://github.com/indexa-git/l10n-dominicana.git
git clone -b 17.0 https://github.com/jorgeyhg/ecf-dgii-para-odoo.git
```

Añade las dos carpetas al `addons_path` de Odoo, actualiza la lista de
aplicaciones e instala **e-CF DGII para Odoo**.

## Puesta en marcha

1. En Odoo, ve a *Ajustes > Facturación > Facturación electrónica (e-CF)* y
   pulsa *Conectar con GLECF*. Se abre GLECF: inicia sesión y pulsa
   *Autorizar*. Vuelves a Odoo ya conectado, sin copiar ninguna clave.

   ¿Aún no tienes cuenta? Pulsa *Crear una cuenta gratis en GLECF*. El registro
   se abre con el nombre, el RNC, la dirección, el teléfono y el correo de tu
   empresa tal como están en Odoo. Confirma tu correo y vuelve a pulsar
   *Conectar con GLECF*.
2. El módulo comprueba que la cuenta de GLECF sea la del mismo RNC que tu
   empresa en Odoo: si no, no envía nada, porque la DGII lo rechazaría. Si
   prefieres, también puedes pegar a mano una clave de API creada en GLECF.
3. Pulsa *Subir certificado*, elige tu certificado digital (.p12) y escribe su
   contraseña. Va directo a GLECF, que lo guarda cifrado; Odoo no guarda ni el
   archivo ni la contraseña.
4. Pulsa *Configurar las secuencias e-NCF* y pon a cada tipo la fecha de
   vencimiento que autorizó la DGII. El primer número de cada tipo se escribe
   en su primera factura; después Odoo sigue solo.
5. Acepta el envío de tus facturas a GLECF.
6. Publica tus facturas como siempre. Cada e-CF se firma y se envía a la DGII, y
   la respuesta vuelve a la factura.

## Tus datos

Nada sale de Odoo hasta que lo aceptas en Ajustes. Al enviar un e-CF se mandan a
GLECF su número, fechas, importes, líneas y los datos de tu empresa y de tu
cliente. Tus facturas siguen en tu Odoo: si dejas GLECF, nada en Odoo queda
bloqueado. Ver la [política de privacidad](https://glecf.com/legal#privacidad).

## Soporte

Escribe a soporte@glecf.com.

## Licencia

Odoo Proprietary License v1.0 (ver `LICENSE` y `COPYRIGHT`).
