# Facturación electrónica e-CF para Odoo (República Dominicana)

Módulo de Odoo 17 que envía a la DGII, como comprobantes fiscales electrónicos
(e-CF), las facturas que publicas en Odoo. Funciona en **Odoo Community y
Enterprise**, sobre la localización dominicana `l10n_do_accounting`.

La firma y el envío los hace [GLECF](https://glecf.com): tu certificado digital
queda en GLECF, no en Odoo.

## Qué hace

- Envía los e-CF tipo 31, 32, 33, 34, 44 y 45, en pesos dominicanos.
- Revisa la factura antes de enviarla: si falta el RNC del cliente o la fecha de
  vencimiento de la secuencia, lo dice en Odoo, no en un rechazo de la DGII.
- Rellena el código de seguridad, la fecha de firma y el código QR de la factura
  impresa, y guarda el XML firmado.
- Muestra el estado ante la DGII en cada factura y en el listado.
- No deja volver a borrador una factura ya enviada a la DGII.

## Instalación

Necesitas Odoo 17 y la localización dominicana:

```bash
git clone -b 17.0 https://github.com/indexa-git/l10n-dominicana.git
git clone -b 17.0 <este repositorio>
```

Añade las dos carpetas al `addons_path` de Odoo, actualiza la lista de
aplicaciones e instala **Dominican e-CF Invoicing**.

## Puesta en marcha

1. Crea tu cuenta gratis en [glecf.com](https://glecf.com/register) y carga ahí
   tu certificado digital.
2. Crea una clave de API en GLECF.
3. En Odoo, ve a *Ajustes > Facturación > Electronic invoicing (e-CF)*, pega la
   clave, pulsa *Check connection* y acepta el envío de tus facturas a GLECF.
4. Publica tus facturas como siempre. Cada e-CF se firma y se envía a la DGII, y
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
