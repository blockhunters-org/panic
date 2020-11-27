import copy
import json
import logging
import sys
from types import FrameType
from typing import List

import pika
import pika.exceptions

from src.alerter.alerters.alerter import Alerter
from src.alerter.alerts.github_alerts import (CannotAccessGitHubPageAlert,
                                              NewGitHubReleaseAlert)
from src.utils.constants import ALERT_EXCHANGE
from src.utils.exceptions import (MessageWasNotDeliveredException,
                                  ReceivedUnexpectedDataException)
from src.utils.logging import log_and_print


class GithubAlerter(Alerter):
    def __init__(self, alerter_name: str, logger: logging.Logger) -> None:
        super().__init__(alerter_name, logger)

    def _initialize_alerter(self) -> None:
        self.rabbitmq.connect_till_successful()
        self.logger.info("Creating \'alert\' exchange")
        self.rabbitmq.exchange_declare(exchange=ALERT_EXCHANGE,
                                       exchange_type='topic', passive=False,
                                       durable=True, auto_delete=False,
                                       internal=False)
        self.logger.info("Creating queue \'github_alerter_queue\'")
        self.rabbitmq.queue_declare('github_alerter_queue', passive=False,
                                    durable=True, exclusive=False,
                                    auto_delete=False)

        self.logger.info("Binding queue \'github_alerter_queue\' to exchange "
                         "\'alerter\' with routing key \'alerter.github\'")
        routing_key = 'alerter.github'
        self.rabbitmq.queue_bind(queue='github_alerter_queue',
                                 exchange=ALERT_EXCHANGE,
                                 routing_key=routing_key)

        # Pre-fetch count is 10 times less the maximum queue size
        prefetch_count = round(self.publishing_queue.maxsize / 5)
        self.rabbitmq.basic_qos(prefetch_count=prefetch_count)

        # Set producing configuration
        self.logger.info("Setting delivery confirmation on RabbitMQ channel")
        self.rabbitmq.confirm_delivery()
        self.logger.info("Declaring consuming intentions")
        self.rabbitmq.basic_consume(queue='github_alerter_queue',
                                    on_message_callback=self._process_data,
                                    auto_ack=False,
                                    exclusive=False,
                                    consumer_tag=None)

    def _process_data(self,
                      ch: pika.adapters.blocking_connection.BlockingChannel,
                      method: pika.spec.Basic.Deliver,
                      properties: pika.spec.BasicProperties,
                      body: bytes) -> None:
        data_received = json.loads(body.decode())
        self.logger.info("Processing {} received from transformers".format(
            data_received))

        processing_error = False
        data_for_alerting = []
        try:
            if 'result' in data_received:
                meta = data_received['result']['meta_data']
                data = data_received['result']['data']
                current = data['no_of_releases']['current']
                previous = data['no_of_releases']['previous']
                if previous is not None and int(current) > int(previous):
                    for i in range(0, current - previous):
                        alert = NewGitHubReleaseAlert(
                            meta['repo_name'],
                            data['releases'][str(i)]['release_name'],
                            data['releases'][str(i)]['tag_name'], 'INFO',
                            meta['last_monitored'], meta['repo_parent_id'],
                            meta['repo_id']
                        )
                        data_for_alerting.append(alert.alert_data)
                        self.logger.debug("Successfully classified alert {}"
                                          "".format(alert.alert_data))
            elif 'error' in data_received:
                meta_data = data_received['error']['meta_data']
                alert = CannotAccessGitHubPageAlert(
                    meta_data['repo_name'], 'ERROR',
                    meta_data['time'],
                    meta_data['repo_parent_id'], meta_data['repo_id']
                )
                data_for_alerting.append(alert.alert_data)
                self.logger.debug("Successfully classified alert {}".format(
                    alert.alert_data)
                )
            else:
                raise ReceivedUnexpectedDataException("{}: _process_data"
                                                      "".format(self))
        except Exception as e:
            self.logger.error("Error when processing {}".format(data_received))
            self.logger.exception(e)
            processing_error = True

        self.rabbitmq.basic_ack(method.delivery_tag, False)

        # Place the data on the publishing queue if there were no processing
        # errors. This is done after acknowledging the data, so that if
        # acknowledgement fails, the data is processed again and we do not have
        # duplication of data in the queue
        if not processing_error:
            self._place_latest_data_on_queue(data_for_alerting)

        # Send any data waiting in the publisher queue, if any
        try:
            self._send_data()
        except MessageWasNotDeliveredException as e:
            # Log the message and do not raise it as message is residing in the
            # publisher queue.
            self.logger.exception(e)
        except Exception as e:
            # For any other exception acknowledge and raise it, so the
            # message is removed from the rabbit queue as this message will now
            # reside in the publisher queue
            raise e

    def _place_latest_data_on_queue(self, data_list: List) -> None:
        # Place the latest alert data on the publishing queue. If the
        # queue is full, remove old data.
        for alert in data_list:
            self.logger.debug("Adding {} to the publishing queue.".format(
                alert))
            if self.publishing_queue.full():
                self.publishing_queue.get()
            self.publishing_queue.put({
                'exchange': ALERT_EXCHANGE,
                'routing_key': 'alert_router.github',
                'data': copy.deepcopy(alert)})
            self.logger.debug("{} added to the publishing queue "
                              "successfully.".format(alert))

    def _alert_classifier_process(self) -> None:
        self._initialize_alerter()
        log_and_print("{} started.".format(self), self.logger)
        while True:
            try:
                self.rabbitmq.start_consuming()
            except pika.exceptions.AMQPChannelError:
                # Error would have already been logged by RabbitMQ logger. If
                # there is a channel error, the RabbitMQ interface creates a
                # new channel, therefore perform another managing round without
                # sleeping
                continue
            except pika.exceptions.AMQPConnectionError as e:
                # Error would have already been logged by RabbitMQ logger.
                # Since we have to re-connect just break the loop.
                raise e
            except MessageWasNotDeliveredException as e:
                # Log the fact that the message could not be sent and re-try
                # another monitoring round without sleeping
                self.logger.exception(e)
                continue
            except Exception as e:
                self.logger.exception(e)
                raise e

    def on_terminate(self, signum: int, stack: FrameType) -> None:
        log_and_print("{} is terminating. Connections with RabbitMQ will be "
                      "closed, and afterwards the process will exit."
                      .format(self), self.logger)

        self.rabbitmq.disconnect_till_successful()
        log_and_print("{} terminated.".format(self), self.logger)
        sys.exit()
