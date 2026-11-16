import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeSanitize from 'rehype-sanitize'

export default function MarkdownView({ text }: { text: string }) {
  return <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}
    skipHtml components={{
      a: props => <a href={props.href} target="_blank" rel="noopener noreferrer nofollow">{props.children}</a>,
      // Remote images can track visitors. Render descriptive text, not remote requests.
      img: props => <span>[Image: {props.alt ?? 'omitted'}]</span>,
    }}>{text}</Markdown></div>
}