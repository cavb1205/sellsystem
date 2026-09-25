"""Orquestador de una entrega de avisos de clientes.

La reserva y la validación usan base de datos; la llamada HTTP ocurre fuera de
la transacción. Si el proceso muere durante la llamada, el evento queda en
``procesando`` y el siguiente intento lo convierte en ``incierto``.
"""
from dataclasses import dataclass

from Clientes.telegram_cola import (
    finalizar_reserva,
    preparar_envio,
    reservar_siguiente,
)
from Clientes.telegram_models import TelegramEvento
from Clientes.telegram_transporte import enviar_texto


@dataclass(frozen=True)
class ResultadoTrabajo:
    evento_id: int
    estado: str
    codigo: str


def procesar_siguiente(vinculo_id):
    reservado = reservar_siguiente(vinculo_id)
    if reservado is None:
        return None
    preparado = preparar_envio(
        reservado.pk, reservado.intentos, reservado.version_vinculo,
    )
    if preparado is None:
        actual = TelegramEvento.objects.filter(pk=reservado.pk).values(
            'estado', 'datos').first()
        if actual and actual['estado'] == 'fallido':
            entrega = actual['datos'].get('entrega', {})
            return ResultadoTrabajo(
                reservado.pk, 'fallido', entrega.get('codigo', 'datos_no_publicables'),
            )
        return ResultadoTrabajo(reservado.pk, 'cancelado', 'vinculo_no_autorizado')
    resultado = enviar_texto(preparado.chat_id, preparado.texto)
    finalizado = finalizar_reserva(
        preparado.evento_id, preparado.intento, preparado.version_vinculo,
        resultado,
    )
    if not finalizado:
        return ResultadoTrabajo(preparado.evento_id, 'cancelado', 'reserva_obsoleta')
    return ResultadoTrabajo(preparado.evento_id, resultado.estado, resultado.codigo)
