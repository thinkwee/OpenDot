import { Component } from 'react'
import { withTranslation } from 'react-i18next'
import Mascot from './Mascot'

// Keeps one broken panel from blanking the whole app.
class Boundary extends Component {
  state = { err: null }
  static getDerivedStateFromError(err) {
    return { err }
  }
  componentDidCatch(err) {
    console.error(err)
  }
  render() {
    if (!this.state.err) return this.props.children
    const { t } = this.props
    return (
      <div className="empty-card" style={{ margin: 12 }}>
        <Mascot animal="fox" color="#FFB38A" status="error" size={56} />
        <p className="muted small">{t('boundary.tripped', { msg: String(this.state.err.message || this.state.err) })}</p>
        <button className="btn sm" onClick={() => this.setState({ err: null })}>{t('common:retry')}</button>
      </div>
    )
  }
}

export default withTranslation('files')(Boundary)
