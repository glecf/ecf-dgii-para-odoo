# e-CF DGII para Odoo: facturación electrónica en República Dominicana

Módulo de **facturación electrónica para Odoo en República Dominicana**. Envía a
la **DGII** (Dirección General de Impuestos Internos) las facturas que publicas
en Odoo como **comprobantes fiscales electrónicos (e-CF)**, tal como exige la
**Ley núm. 32-23 de Facturación Electrónica** y su reglamento, el **Decreto
587-24**.

> **Solo con un plan de pago de GLECF** (Licencia Anual o plan a medida). El
> módulo se descarga gratis, pero el Plan Gratis de GLECF no incluye Odoo.
> Planes: [glecf.com/precios](https://glecf.com/precios/).

> **En mejora continua.** El módulo funciona y se sigue ampliando. El manual,
> con capturas: [docs/manual.md](docs/manual.md).

Funciona en **Odoo 18 Community y Enterprise**, con la localización dominicana
incluida. ¿Usas Odoo 17? Está en la rama
[17.0](https://github.com/glecf/ecf-dgii-para-odoo/tree/17.0). La firma y el
envío a la DGII los hace
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
- Trae los **e-CF que te mandan tus proveedores** como facturas de proveedor en
  borrador (y sus notas de crédito y débito, enlazadas a la factura), con el
  e-NCF del proveedor listo para el 606 y el XML adjunto. Desde la factura los
  aceptas o los rechazas: la respuesta va a la DGII y al proveedor.
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

Necesitas Odoo 18. La localización dominicana ya viene en este repositorio:

```bash
git clone -b 18.0 https://github.com/glecf/ecf-dgii-para-odoo.git
```

Añade esa carpeta al `addons_path` de Odoo, actualiza la lista de aplicaciones e
instala **e-CF DGII para Odoo**. La localización se instala con él.

Si el PDF de la factura sale sin QR o sin diseño, mira la sección «La factura
impresa» del [manual](docs/manual.md).

## Puesta en marcha

1. En Odoo, ve a *Ajustes > Facturación > Facturación electrónica (e-CF)* y
   pulsa *Conectar con GLECF*. Se abre GLECF: inicia sesión y pulsa
   *Autorizar*. Vuelves a Odoo ya conectado, sin copiar ninguna clave.

   ¿Aún no tienes cuenta? Pulsa *Crear una cuenta gratis en GLECF*. El registro
   se abre con el nombre, el RNC, la dirección, el teléfono y el correo de tu
   empresa tal como están en Odoo. Confirma tu correo, contrata la Licencia
   Anual (el Plan Gratis no incluye Odoo) y vuelve a pulsar *Conectar con
   GLECF*.
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

## Direcciones para la DGII

Tus e-CF salen por la API de GLECF (`api.glecf.com`). Para que los e-CF de tus
proveedores y las aprobaciones comerciales lleguen a GLECF, en la DGII se
registran estas direcciones. En cada caja se escribe **solo `api.glecf.com`**:
la DGII ya pone `https://` y completa el resto.

| Campo de la DGII | Qué escribir |
|---|---|
| URL Recepción ECF | `api.glecf.com` |
| URL Aprobación Comercial | `api.glecf.com` |
| URL Autentificación (opcional) | `api.glecf.com` |

Si ya eres emisor electrónico, se cambian en la Oficina Virtual, en
«Mantenimiento Directorio de Facturadores Electrónicos». Si te estás
certificando, van en el Portal de Certificación, durante el set de pruebas. El
módulo te dice en Ajustes si ya están registradas.

## Tus datos

Nada sale de Odoo hasta que lo aceptas en Ajustes. Al enviar un e-CF se mandan a
GLECF su número, fechas, importes, líneas y los datos de tu empresa y de tu
cliente. Tus facturas siguen en tu Odoo: si dejas GLECF, nada en Odoo queda
bloqueado. Ver la [política de privacidad](https://glecf.com/legal#privacidad).

## Soporte

Escribe a soporte@glecf.com.

## Licencia

El módulo `l10n_do_ecf_glecf`: Odoo Proprietary License v1.0 (ver `LICENSE` y
`COPYRIGHT`).

La localización `l10n_do_accounting` es código libre de iterativo LLC e Indexa,
adaptado a Odoo 18, bajo su licencia LGPL-3 (ver `l10n_do_accounting/LICENSE`).
