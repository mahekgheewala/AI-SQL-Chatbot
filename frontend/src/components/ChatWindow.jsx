import React, { useEffect, useRef } from 'react';
import MessageBubble from './MessageBubble';

/**
 * ChatWindow — renders the scrollable message list.
 *
 * Phase 4 addition: threads confirmation state props from App.jsx
 * down to each MessageBubble without owning any state itself.
 */
export default function ChatWindow({
  messages,
  confirmedMessages = new Set(),
  cancelledMessages = new Set(),
  onConfirm,
  onCancel,
}) {
  const endOfMessagesRef = useRef(null);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-6 bg-gray-50">
      <div className="max-w-4xl mx-auto">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 mt-20">
            <div className="text-4xl mb-4">💬</div>
            <p>Send a message to start the conversation.</p>
          </div>
        ) : (
          messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              isConfirmed={confirmedMessages.has(msg.id)}
              isCancelled={cancelledMessages.has(msg.id)}
              onConfirm={onConfirm ? () => onConfirm(msg.id) : undefined}
              onCancel={onCancel ? () => onCancel(msg.id) : undefined}
            />
          ))
        )}
        <div ref={endOfMessagesRef} />
      </div>
    </div>
  );
}

