// Bootstrap 6's own PostCSS step (build/postcss.config.mjs in twbs/bootstrap
// v6.0.0-alpha.1): prefix every custom property with --bs-, as the CDN build is.
import postcssPrefixCustomProperties from "postcss-prefix-custom-properties"
import autoprefixer from "autoprefixer"

export default {
  plugins: [
    postcssPrefixCustomProperties({prefix: "bs-", ignore: [/^--bs-/, /^--bd-/]}),
    autoprefixer({cascade: false}),
  ],
}
