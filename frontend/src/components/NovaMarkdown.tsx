import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** 模型回答以 Markdown 呈现；原始 HTML 不参与渲染。 */
export default function NovaMarkdown({ content,onConversation }: { content: string;onConversation?:(sessionId:string)=>void }) {
  return <div className="nova-markdown">
    <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
      table: ({ children }) => <div className="nova-table-scroll" role="region" aria-label="回答数据表格" tabIndex={0}><table>{children}</table></div>,
      a: ({ children, href }) => href?.match(/^#nova-conversation=(NOVA-[A-Za-z0-9-]+)$/)&&onConversation
        ?<button type="button" className="secondary" onClick={()=>onConversation(href.slice('#nova-conversation='.length))}>{children}</button>
        :<a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
      img: ({ alt }) => <span>{alt || '图片'}</span>,
    }}>{content}</Markdown>
  </div>;
}
